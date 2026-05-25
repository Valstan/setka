"""Unit tests для rolling discovery (daily beat-таска, 1 регион в день).

Покрываем:

- ``_select_oldest_discovery_region`` — выбор регионов с фильтрацией по
  is_active / vk_group_id / config; сортировка NULLS FIRST по last_discovery_at.
- ``discover_rolling_one_region_async`` — happy path (вызов runner, апдейт
  last_discovery_at, дельта new_pending, Telegram alert при new > 0).
- ``_format_rolling_message`` — pure helper.

Не пытаемся тестировать Telegram-отправку (request mocked в _maybe_send_*),
не пытаемся тестировать saved Region (real DB сериализация не нужна для
этого слоя).
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from database.models import Region
from tasks import discovery_tasks as dt

# ───────── helpers ─────────


def _r(*, id_=1, code="mi", last_disc=None, is_active=True, vk_gid=-1, center="Х", localities=None):
    cfg = {}
    if localities is not None:
        cfg["localities"] = localities
    r = Region(
        id=id_,
        code=code,
        name=f"R-{code}",
        is_active=is_active,
        vk_group_id=vk_gid,
        center_city=center,
        config=cfg,
    )
    r.last_discovery_at = last_disc
    return r


class _Session:
    """Минимальный async-stub: scalars().all() для select(Region),
    scalar() для COUNT, execute(update(…)) noop, commit() noop."""

    def __init__(self, *, regions=None, count_returns=None):
        self._regions = regions or []
        self._counts = list(count_returns or [])
        self.committed = False
        self.updates: list = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def execute(self, stmt):
        # Differentiate by statement-string sniffing — простой и достаточный
        # для тестов; SQLAlchemy core update() vs select() видны через repr.
        s = str(stmt).lower()
        result = MagicMock()
        if "update" in s.split()[0]:
            self.updates.append(stmt)
            return result
        if "count(" in s:
            value = self._counts.pop(0) if self._counts else 0
            result.scalar.return_value = value
            return result
        # select(Region) → возвращаем регионы из конструктора.
        scalars = MagicMock()
        scalars.all.return_value = self._regions
        result.scalars.return_value = scalars
        return result

    async def commit(self):
        self.committed = True


# ───────── _select_oldest_discovery_region ─────────


@pytest.mark.asyncio
async def test_select_skips_inactive_regions():
    """Регион is_active=False НЕ должен попасть в выборку — фильтр в SQL,
    но если бы попал, его всё равно нужно отсеять по конфигу."""
    s = _Session(regions=[])
    assert await dt._select_oldest_discovery_region(s) is None


@pytest.mark.asyncio
async def test_select_skips_regions_without_localities():
    """Регионы с пустым/отсутствующим config.localities — discovery
    физически невозможен, пропускаем."""
    regs = [
        _r(id_=1, code="a", localities=None),  # config={}
        _r(id_=2, code="b", localities=[]),  # пустой list
        _r(id_=3, code="c", localities=["X"], center=None),  # нет center_city
    ]
    s = _Session(regions=regs)
    assert await dt._select_oldest_discovery_region(s) is None


@pytest.mark.asyncio
async def test_select_returns_null_last_discovery_first():
    """Регион с last_discovery_at=NULL имеет priority перед регионом со
    свежим last_discovery_at — «никогда не запускали» > «давно»."""
    regs = [
        _r(id_=1, code="a", localities=["X"], last_disc=datetime(2026, 5, 1)),
        _r(id_=2, code="b", localities=["X"], last_disc=None),
        _r(id_=3, code="c", localities=["X"], last_disc=datetime(2026, 4, 1)),
    ]
    s = _Session(regions=regs)
    chosen = await dt._select_oldest_discovery_region(s)
    assert chosen is not None
    assert chosen.code == "b"


@pytest.mark.asyncio
async def test_select_returns_oldest_among_non_null():
    """Среди уже-проверенных выбирается самый давний."""
    regs = [
        _r(id_=1, code="a", localities=["X"], last_disc=datetime(2026, 5, 20)),
        _r(id_=2, code="b", localities=["X"], last_disc=datetime(2026, 5, 1)),
        _r(id_=3, code="c", localities=["X"], last_disc=datetime(2026, 5, 10)),
    ]
    s = _Session(regions=regs)
    chosen = await dt._select_oldest_discovery_region(s)
    assert chosen.code == "b"


@pytest.mark.asyncio
async def test_select_ties_broken_by_code():
    """При равных last_discovery_at сортируем по code (детерминизм)."""
    same = datetime(2026, 5, 1)
    regs = [
        _r(id_=1, code="z", localities=["X"], last_disc=same),
        _r(id_=2, code="a", localities=["X"], last_disc=same),
        _r(id_=3, code="m", localities=["X"], last_disc=same),
    ]
    s = _Session(regions=regs)
    chosen = await dt._select_oldest_discovery_region(s)
    assert chosen.code == "a"


# ───────── discover_rolling_one_region_async ─────────


@pytest.mark.asyncio
async def test_rolling_no_eligible_returns_skipped():
    """Когда нет подходящих регионов — корректно возвращает skipped."""
    s = _Session(regions=[])
    with patch.object(dt, "AsyncSessionLocal", return_value=s):
        out = await dt.discover_rolling_one_region_async(send_telegram=False)
    assert out == {"success": True, "skipped": "no eligible regions"}


@pytest.mark.asyncio
async def test_rolling_happy_path_updates_last_discovery_and_reports_delta():
    """Happy path: runner возвращает success, дельта pending считается,
    last_discovery_at обновляется."""
    region = _r(id_=42, code="mi", localities=["Шешурга"], last_disc=None)
    # Первая сессия: get region + count_before=5. Вторая сессия: update + count_after=12.
    s1 = _Session(regions=[region], count_returns=[5])
    s2 = _Session(count_returns=[12])
    sessions = iter([s1, s2])

    with (
        patch.object(dt, "AsyncSessionLocal", side_effect=lambda: next(sessions)),
        patch.object(
            dt,
            "run_discovery_for_region_async",
            AsyncMock(
                return_value={
                    "success": True,
                    "region": "mi",
                    "found": 8,
                    "inserted": 7,
                    "refreshed": 1,
                }
            ),
        ),
        patch.object(dt, "_maybe_send_rolling_telegram_alert") as alert_mock,
    ):
        out = await dt.discover_rolling_one_region_async()

    assert out["success"] is True
    assert out["region"] == "mi"
    assert out["region_id"] == 42
    assert out["new_pending"] == 7  # 12 - 5
    assert out["total_pending"] == 12
    assert out["inserted"] == 7
    # Telegram-alert должен быть вызван (new_pending > 0)
    alert_mock.assert_called_once()
    # last_discovery_at обновлён через update statement
    assert s2.committed is True
    assert len(s2.updates) == 1


@pytest.mark.asyncio
async def test_rolling_skips_telegram_when_no_new_candidates():
    """Если runner ничего нового не нашёл — Telegram молчит (иначе пользователю
    каждый день будет прилетать «0 новых» и шум обесценит уведомления)."""
    region = _r(id_=7, code="x", localities=["Y"], last_disc=datetime(2026, 5, 1))
    s1 = _Session(regions=[region], count_returns=[10])
    s2 = _Session(count_returns=[10])  # дельта = 0
    sessions = iter([s1, s2])

    with (
        patch.object(dt, "AsyncSessionLocal", side_effect=lambda: next(sessions)),
        patch.object(
            dt,
            "run_discovery_for_region_async",
            AsyncMock(return_value={"success": True, "region": "x", "found": 3, "inserted": 0}),
        ),
        patch.object(dt, "_maybe_send_rolling_telegram_alert") as alert_mock,
    ):
        out = await dt.discover_rolling_one_region_async()

    assert out["new_pending"] == 0
    alert_mock.assert_not_called()


@pytest.mark.asyncio
async def test_rolling_respects_send_telegram_false():
    """send_telegram=False (для манульного запуска через CLI) — не дёргает alert."""
    region = _r(id_=7, code="x", localities=["Y"])
    s1 = _Session(regions=[region], count_returns=[0])
    s2 = _Session(count_returns=[5])
    sessions = iter([s1, s2])

    with (
        patch.object(dt, "AsyncSessionLocal", side_effect=lambda: next(sessions)),
        patch.object(
            dt,
            "run_discovery_for_region_async",
            AsyncMock(return_value={"success": True, "region": "x", "inserted": 5}),
        ),
        patch.object(dt, "_maybe_send_rolling_telegram_alert") as alert_mock,
    ):
        out = await dt.discover_rolling_one_region_async(send_telegram=False)

    assert out["new_pending"] == 5
    alert_mock.assert_not_called()


# ───────── _format_rolling_message ─────────


def test_format_rolling_message_contains_essentials():
    msg = dt._format_rolling_message(
        {
            "region": "tuzha",
            "region_name": "Тужа, Кировская область",
            "new_pending": 8,
            "total_pending": 24,
        }
    )
    assert "tuzha" in msg
    assert "Тужа" in msg
    assert "8" in msg
    assert "24" in msg
    assert "/regions/tuzha/discovery" in msg
