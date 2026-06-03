"""Тесты quick-action «применить suggested_category» + сериализации health-полей.

Сессия БД и кэш мокаются (``AsyncMock``) — без реальной БД и Redis, в стиле
``tests/test_api/test_suggest_neighbors.py`` (unit-тесты проекта не поднимают
SQLite; мокаем сессию). Покрываем:

* ``_community_to_dict`` — новые поля ``health_status`` / ``suggested_category``
  в ответе, дефолт ``health_status='active'`` при ``None``;
* ``apply_suggested_category`` — перенос подсказки в ``category``, сброс статуса
  в ``active``, очистка подсказки; 404 для неизвестного id; 400 без подсказки;
* ``get_all_communities`` — фильтр ``health_status`` не ломает выдачу и
  сериализует health-поля.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from web.api import communities as communities_api

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _result_scalar_one(obj):
    r = MagicMock()
    r.scalar_one_or_none.return_value = obj
    return r


def _result_scalars_all(objs):
    r = MagicMock()
    r.scalars.return_value.all.return_value = objs
    return r


def _community(
    *,
    cid=1,
    category="novost",
    health_status="changed_category",
    suggested_category="reklama",
    telegram_channel=None,
    telegram_bot=None,
    region=SimpleNamespace(code="mi", name="Малмыж"),
):
    return SimpleNamespace(
        id=cid,
        region_id=7,
        region=region,
        vk_id=-123,
        screen_name="club123",
        name="Тест-сообщество",
        category=category,
        is_active=True,
        health_status=health_status,
        suggested_category=suggested_category,
        last_checked=datetime(2026, 5, 31, 12, 0, 0),
        posts_count=42,
        created_at=datetime(2026, 5, 1, 9, 0, 0),
        updated_at=datetime(2026, 5, 1, 9, 0, 0),
        telegram_channel=telegram_channel,
        telegram_bot=telegram_bot,
    )


@pytest.fixture(autouse=True)
def _no_cache(monkeypatch):
    """Кэш не должен лезть в Redis под тестом.

    ``invalidate_cache`` мокаем напрямую; ``@cache``-декоратор на
    ``get_all_communities`` дёргает ``utils.cache.get_cache`` в рантайме —
    подменяем фейком (miss → выполняем функцию, set — no-op).
    """
    import utils.cache as cache_module

    monkeypatch.setattr(communities_api, "invalidate_cache", AsyncMock())
    fake_cache = SimpleNamespace(get=AsyncMock(return_value=None), set=AsyncMock())
    monkeypatch.setattr(cache_module, "get_cache", lambda: fake_cache)


# ---------------------------------------------------------------------------
# _community_to_dict
# ---------------------------------------------------------------------------


def test_to_dict_includes_health_fields():
    d = communities_api._community_to_dict(_community())
    assert d["health_status"] == "changed_category"
    assert d["suggested_category"] == "reklama"
    assert d["region_code"] == "mi"
    assert d["last_checked"] == "2026-05-31T12:00:00"


def test_to_dict_defaults_health_status_to_active():
    d = communities_api._community_to_dict(_community(health_status=None, suggested_category=None))
    assert d["health_status"] == "active"
    assert d["suggested_category"] is None


def test_to_dict_includes_telegram_fields():
    d = communities_api._community_to_dict(
        _community(telegram_channel="@gonba_life", telegram_bot="VALSTANBOT")
    )
    assert d["telegram_channel"] == "@gonba_life"
    assert d["telegram_bot"] == "VALSTANBOT"


def test_to_dict_telegram_fields_none_by_default():
    d = communities_api._community_to_dict(_community())
    assert d["telegram_channel"] is None
    assert d["telegram_bot"] is None


# ---------------------------------------------------------------------------
# update_community — Telegram-зеркало
# ---------------------------------------------------------------------------


async def test_update_sets_telegram_mirror():
    community = _community(telegram_channel=None, telegram_bot=None)
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_result_scalar_one(community))

    payload = communities_api.CommunityUpdate(
        telegram_channel="@gonba_life", telegram_bot="VALSTANBOT"
    )
    resp = await communities_api.update_community(
        community_id=1, community_data=payload, db=session
    )

    assert community.telegram_channel == "@gonba_life"
    assert community.telegram_bot == "VALSTANBOT"
    assert resp["telegram_channel"] == "@gonba_life"
    session.commit.assert_awaited_once()


async def test_update_clears_telegram_mirror_with_empty_string():
    community = _community(telegram_channel="@gonba_life", telegram_bot="VALSTANBOT")
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_result_scalar_one(community))

    # Пустая строка снимает зеркало → NULL (не игнорируется как None в общем цикле).
    payload = communities_api.CommunityUpdate(telegram_channel="  ", telegram_bot="")
    await communities_api.update_community(community_id=1, community_data=payload, db=session)

    assert community.telegram_channel is None
    assert community.telegram_bot is None


async def test_update_telegram_does_not_touch_other_fields():
    community = _community(category="novost", telegram_channel=None)
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_result_scalar_one(community))

    payload = communities_api.CommunityUpdate(telegram_channel="@x")
    await communities_api.update_community(community_id=1, community_data=payload, db=session)

    assert community.telegram_channel == "@x"
    assert community.category == "novost"  # не затронуто (exclude_unset)


# ---------------------------------------------------------------------------
# apply_suggested_category
# ---------------------------------------------------------------------------


async def test_apply_moves_suggestion_into_category():
    community = _community(category="novost", suggested_category="reklama")
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_result_scalar_one(community))

    resp = await communities_api.apply_suggested_category(community_id=1, db=session)

    # Подсказка применена, статус и подсказка сброшены.
    assert community.category == "reklama"
    assert community.suggested_category is None
    assert community.health_status == "active"
    session.commit.assert_awaited_once()
    communities_api.invalidate_cache.assert_awaited_once()
    # Ответ отражает применённое состояние.
    assert resp["category"] == "reklama"
    assert resp["health_status"] == "active"
    assert resp["suggested_category"] is None


async def test_apply_404_for_unknown_community():
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_result_scalar_one(None))

    with pytest.raises(HTTPException) as exc:
        await communities_api.apply_suggested_category(community_id=999, db=session)
    assert exc.value.status_code == 404
    session.commit.assert_not_awaited()


async def test_apply_400_when_no_suggestion():
    community = _community(suggested_category=None, health_status="active")
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_result_scalar_one(community))

    with pytest.raises(HTTPException) as exc:
        await communities_api.apply_suggested_category(community_id=1, db=session)
    assert exc.value.status_code == 400
    session.commit.assert_not_awaited()


# ---------------------------------------------------------------------------
# get_all_communities — health_status filter
# ---------------------------------------------------------------------------


async def test_list_with_health_status_filter_serializes_fields():
    community = _community()
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_result_scalars_all([community]))

    rows = await communities_api.get_all_communities(health_status="changed_category", db=session)

    assert len(rows) == 1
    assert rows[0]["health_status"] == "changed_category"
    assert rows[0]["suggested_category"] == "reklama"
    # Запрос выполнен (фильтр прошёл в where без падения).
    session.execute.assert_awaited_once()
