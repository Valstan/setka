"""Тесты соседей: нормализация кодов + двунаправленная (обоюдная) синхронизация.

Движок соседского обмена (``modules.cascaded_digest.run_neighbor_digest``) матчит
соседей по ``Region.code.in_(codes)`` — значит в ``Region.neighbors`` должны лежать
**коды** регионов, а связь должна быть обоюдной (если A→B, то и B→A). Покрываем:

* ``_normalize_neighbor_codes`` — резолв кода/русского названия/center_city → код,
  отброс неизвестных и само-соседа, дедуп, сортировка.
* ``_sync_bidirectional_neighbors`` — добавление/удаление зеркалит у соседа.

Сессия БД мокается через ``AsyncMock`` (как в ``tests/test_cascaded_digest.py`` —
в проекте unit-тесты не поднимают реальную БД).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from web.api import regions as regions_api


def _session_returning_rows(rows):
    """Mock-сессия, чей ``execute(...).all()`` возвращает заданные строки.

    ``rows`` — список кортежей ``(code, name, center_city)`` (как отдаёт
    ``select(Region.code, Region.name, Region.center_city)``).
    """
    session = AsyncMock()
    result = MagicMock()
    result.all.return_value = rows
    session.execute = AsyncMock(return_value=result)
    return session


def _session_returning_objects(objs):
    """Mock-сессия, чей ``execute(...).scalars().all()`` возвращает объекты Region."""
    session = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = objs
    session.execute = AsyncMock(return_value=result)
    return session


def _neighbors(obj) -> set:
    return set(regions_api._parse_neighbor_tokens(obj.neighbors))


# ---------------------------------------------------------------------------
# _parse_neighbor_tokens
# ---------------------------------------------------------------------------


def test_parse_tokens_handles_commas_semicolons_and_blanks():
    assert regions_api._parse_neighbor_tokens(None) == []
    assert regions_api._parse_neighbor_tokens("") == []
    assert regions_api._parse_neighbor_tokens(" a , b ; c ,, ") == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# _normalize_neighbor_codes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_normalize_resolves_codes_and_drops_unknown():
    session = _session_returning_rows(
        [
            ("vp", "ВЯТСКИЕ ПОЛЯНЫ", None),
            ("mi", "МАЛМЫЖ", None),
            ("ur", "УРЖУМ", None),
        ]
    )
    codes = await regions_api._normalize_neighbor_codes(session, "vp, mi, zzz_unknown", "bal")
    assert codes == ["mi", "vp"]  # отсортировано, неизвестный токен отброшен


@pytest.mark.asyncio
async def test_normalize_resolves_russian_names():
    # Исторически neighbors забивали русскими названиями — должны резолвиться в коды.
    session = _session_returning_rows(
        [
            ("kukmor", "Кукмор", None),
            ("bal", "Балтаси", None),
            ("vp", "ВЯТСКИЕ ПОЛЯНЫ", None),
        ]
    )
    codes = await regions_api._normalize_neighbor_codes(session, "Кукмор, балтаси", "vp")
    assert codes == ["bal", "kukmor"]


@pytest.mark.asyncio
async def test_normalize_resolves_by_center_city():
    session = _session_returning_rows(
        [
            ("mi", "МАЛМЫЖ - ИНФО", "Малмыж"),
            ("vp", "ВП", None),
        ]
    )
    codes = await regions_api._normalize_neighbor_codes(session, "малмыж", "vp")
    assert codes == ["mi"]


@pytest.mark.asyncio
async def test_normalize_drops_self_and_dedups():
    session = _session_returning_rows(
        [
            ("vp", "ВЯТСКИЕ ПОЛЯНЫ", None),
            ("mi", "МАЛМЫЖ", None),
        ]
    )
    codes = await regions_api._normalize_neighbor_codes(session, "vp, mi, mi, МАЛМЫЖ, vp", "vp")
    assert codes == ["mi"]  # self (vp) убран, дубли схлопнуты


@pytest.mark.asyncio
async def test_normalize_empty_returns_empty():
    session = _session_returning_rows([("vp", "ВП", None)])
    assert await regions_api._normalize_neighbor_codes(session, None, "vp") == []
    assert await regions_api._normalize_neighbor_codes(session, "  ,; ,", "vp") == []


# ---------------------------------------------------------------------------
# _sync_bidirectional_neighbors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_add_makes_reciprocal():
    b = SimpleNamespace(code="b", neighbors=None)
    c = SimpleNamespace(code="c", neighbors=None)
    session = _session_returning_objects([b, c])
    await regions_api._sync_bidirectional_neighbors(session, "a", None, ["b", "c"])
    assert _neighbors(b) == {"a"}
    assert _neighbors(c) == {"a"}


@pytest.mark.asyncio
async def test_sync_remove_clears_reciprocal():
    b = SimpleNamespace(code="b", neighbors="a")
    session = _session_returning_objects([b])
    # a убрал b из соседей → у b тоже должно исчезнуть a.
    await regions_api._sync_bidirectional_neighbors(session, "a", "b", [])
    assert _neighbors(b) == set()


@pytest.mark.asyncio
async def test_sync_partial_change():
    b = SimpleNamespace(code="b", neighbors="a")
    c = SimpleNamespace(code="c", neighbors=None)
    session = _session_returning_objects([b, c])
    # a: было [b], стало [c] → b теряет a, c получает a.
    await regions_api._sync_bidirectional_neighbors(session, "a", "b", ["c"])
    assert _neighbors(b) == set()
    assert _neighbors(c) == {"a"}


@pytest.mark.asyncio
async def test_sync_noop_when_nothing_changed():
    """Если набор соседей не изменился — execute даже не вызывается (нет затронутых)."""
    session = _session_returning_objects([])
    await regions_api._sync_bidirectional_neighbors(session, "a", "b,c", ["b", "c"])
    session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_sync_preserves_existing_other_neighbors():
    """Зеркаля A у соседа B, не затираем уже имеющихся у B соседей."""
    b = SimpleNamespace(code="b", neighbors="x,y")
    session = _session_returning_objects([b])
    await regions_api._sync_bidirectional_neighbors(session, "a", None, ["b"])
    assert _neighbors(b) == {"a", "x", "y"}
