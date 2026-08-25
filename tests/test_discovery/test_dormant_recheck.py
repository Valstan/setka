"""Ре-проверка авто-вынесенных сообществ (рекомендация brain 2026-08-22, пул #182).

Покрываем ровно то свойство, ради которого задача заведена: **автоматический
вердикт, выводящий предмет из области наблюдения, обязан быть отзывным.**
Поэтому центральный тест — не «задача отработала», а «ожившее сообщество
вернулось в парс», плюс контроль-негативы на все остальные судьбы: спящее,
недоступное и transient-ошибка ничего не должны менять по ошибке.

Отдельно закреплена ловушка закреплённого поста: VK отдаёт первым pinned-пост,
который часто старше остальных, и чтение ``items[0]`` объявило бы живое
сообщество спящим.
"""

from __future__ import annotations

from calendar import timegm
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from modules.discovery import dormant_outcomes as do
from tasks import discovery_tasks as dt
from tests.test_discovery.test_recheck_tasks import _FakeSession, _make_community

DISABLED_AT = datetime(2026, 6, 1, 12, 0)


def _make_disabled(*, id_=1, vk_id=100, last_post_at=None):
    c = _make_community(id_=id_, vk_id=vk_id, is_active=False)
    c.disabled_at = DISABLED_AT
    c.disabled_reason = "dormant_t1_auto"
    c.health_status = "dormant"
    c.last_post_at = last_post_at or (DISABLED_AT - timedelta(days=400))
    return c


def _to_unix_utc(d: datetime) -> int:
    """Наивный UTC -> unix-timestamp.

    Через ``calendar.timegm``, а НЕ через ``datetime.timestamp()``: последний
    трактует наивное время как МЕСТНОЕ, и на машине в MSK тест разъезжается с
    кодом ровно на три часа. Тот же класс, что и на проде, где ``now()`` в psql
    — MSK, а ``created_at`` пишется наивным UTC.
    """
    return timegm(d.timetuple())


def _wall(*dts):
    return {"items": [{"date": _to_unix_utc(d)} for d in dts]}


def _err(code, msg="boom"):
    return {"error": {"error_code": code, "error_msg": msg}}


async def _run(community, resp, *, revive=True):
    session = _FakeSession([{"kind": "scalars_all", "value": [community]}])

    async def fake_wall_get(_client, _owner_id, _count):
        return resp

    with (
        patch.object(dt, "_pick_parse_token", return_value="tok"),
        patch.object(dt, "AsyncSessionLocal", return_value=session),
        patch.object(dt, "VKClient", MagicMock()),
        patch.object(do, "wall_get", side_effect=fake_wall_get),
        patch.object(dt, "_send_telegram_html", MagicMock()),
    ):
        return await dt.dormant_recheck_disabled_async(send_telegram=False, revive=revive)


# ───────── судьба «ожил» — вердикт отзывается ─────────


@pytest.mark.asyncio
async def test_revived_community_returns_to_parsing():
    c = _make_disabled()
    fresh = DISABLED_AT + timedelta(days=20)

    res = await _run(c, _wall(fresh))

    assert res["revived"] == 1
    assert c.is_active is True
    assert c.disabled_at is None
    assert c.disabled_reason is None
    assert c.health_status == "active"
    assert c.last_post_at == fresh


@pytest.mark.asyncio
async def test_revive_false_reports_but_does_not_touch_flag():
    """Режим наблюдения: судьбу считаем, но в парс не возвращаем."""
    c = _make_disabled()
    res = await _run(c, _wall(DISABLED_AT + timedelta(days=5)), revive=False)

    assert res["revived"] == 1
    assert c.is_active is False
    assert c.disabled_reason == "dormant_t1_auto"


# ───────── контроль-негативы: остальные судьбы ничего не меняют ─────────


@pytest.mark.asyncio
async def test_asleep_community_stays_disabled():
    c = _make_disabled()
    res = await _run(c, _wall(DISABLED_AT - timedelta(days=100)))

    assert res["asleep"] == 1
    assert res["revived"] == 0
    assert c.is_active is False
    assert c.disabled_reason == "dormant_t1_auto"


@pytest.mark.asyncio
async def test_empty_wall_stays_disabled():
    c = _make_disabled()
    res = await _run(c, {"items": []})

    assert res["asleep"] == 1
    assert c.is_active is False


@pytest.mark.asyncio
async def test_unreachable_is_recorded_as_dead_but_not_revived():
    c = _make_disabled()
    dead_code = sorted(do.DEAD_ERROR_CODES)[0]

    res = await _run(c, _err(dead_code, "group was deleted"))

    assert res["unreachable"] == 1
    assert c.is_active is False
    assert c.health_status == "dead"
    assert c.last_error_code == dead_code


@pytest.mark.asyncio
async def test_transient_error_changes_nothing():
    c = _make_disabled()
    before = (c.is_active, c.health_status, c.disabled_reason)

    res = await _run(c, _err(6, "Too many requests per second"))

    assert res["unknown"] == 1
    assert (c.is_active, c.health_status, c.disabled_reason) == before


# ───────── ловушка закреплённого поста ─────────


def test_newest_post_takes_max_not_first_item():
    """VK отдаёт pinned первым, и он часто старше — items[0] соврал бы."""
    pinned = DISABLED_AT - timedelta(days=300)
    fresh = DISABLED_AT + timedelta(days=3)

    got = do.newest_post_dt([{"date": _to_unix_utc(pinned)}, {"date": _to_unix_utc(fresh)}])

    assert got == fresh


@pytest.mark.asyncio
async def test_pinned_older_post_does_not_hide_revival():
    c = _make_disabled()
    pinned = DISABLED_AT - timedelta(days=300)
    fresh = DISABLED_AT + timedelta(days=3)

    res = await _run(c, _wall(pinned, fresh))

    assert res["revived"] == 1
    assert c.is_active is True


# ───────── пустая выборка и отсутствие токена ─────────


@pytest.mark.asyncio
async def test_no_disabled_rows_is_silent():
    session = _FakeSession([{"kind": "scalars_all", "value": []}])
    with (
        patch.object(dt, "_pick_parse_token", return_value="tok"),
        patch.object(dt, "AsyncSessionLocal", return_value=session),
        patch.object(dt, "VKClient", MagicMock()),
    ):
        res = await dt.dormant_recheck_disabled_async(send_telegram=False)

    assert res["success"] is True
    assert res["checked"] == 0


@pytest.mark.asyncio
async def test_missing_token_does_not_crash():
    with patch.object(dt, "_pick_parse_token", return_value=None):
        res = await dt.dormant_recheck_disabled_async(send_telegram=False)

    assert res["success"] is False
    assert res["error"] == "no_token"


# ───────── расписание ─────────


def test_recheck_runs_before_monthly_digest():
    """Иначе digest отрапортует как вынесенное то, что ре-проверка тут же вернёт."""
    from tasks.celery_app import app

    sched = app.conf.beat_schedule
    recheck = sched["dormant-recheck-disabled-monthly"]["schedule"]
    digest = sched["dormant-disable-digest-monthly"]["schedule"]

    assert recheck.hour == digest.hour
    assert min(recheck.minute) < min(digest.minute)


# ───────── флип-флоп: утверждение из докстринга, проверенное, а не заявленное ─────────


@pytest.mark.asyncio
async def test_revived_community_is_not_re_disabled_by_next_weekly_recheck():
    """Ре-проверка вернула — weekly-recheck не имеет права вынести обратно.

    Утверждение живёт в докстринге ``dormant_recheck_disabled_async``; здесь оно
    проверено. Защиты две, и достаточно любой: (1) авто-вынос требует ДВУХ подряд
    ``dormant``, а у возвращённого ``health_status='active'``; (2) он требует tier
    T1, а у возвращённого свежий ``last_post_at``.
    """
    from modules.discovery.health_check import classify_dormant_tier

    c = _make_disabled()
    fresh = DISABLED_AT + timedelta(days=20)
    await _run(c, _wall(fresh))

    assert c.health_status == "active"  # (1) предыдущий статус больше не dormant
    assert classify_dormant_tier(c.last_post_at, now=fresh + timedelta(days=1)) != "t1"  # (2)
