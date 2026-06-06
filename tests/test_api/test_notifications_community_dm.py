"""Тесты ленты входящих ЛС из БД (web/api/notifications.get_community_dm_inbox).

Источник уведомлений о ЛС — наш стор (``ad_requests``, route='notifications'), а
не живой VK unread-счётчик. Сессия БД мокается (AsyncMock), как в test_ad_cabinet.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from database.models import AdRequest
from web.api import notifications as api


def _scalars_all(objs):
    r = MagicMock()
    r.scalars.return_value.all.return_value = objs
    return r


def _dm(**kw):
    defaults = dict(
        id=10,
        origin="inbound_dm",
        route="notifications",
        community_vk_id=-100,
        community_name="Малмыж Инфо",
        vk_post_id=None,
        peer_id=42,
        author_vk_id=42,
        author_name="Иван",
        author_is_group=False,
        text_snapshot="привет, когда дайджест?",
        status="new",
        handling_status="new",
        last_message_id=555,
    )
    defaults.update(kw)
    return AdRequest(**defaults)


async def test_community_dm_inbox_serializes_unhandled():
    ar = _dm()
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalars_all([ar]))
    out = await api.get_community_dm_inbox(db=db)
    assert len(out["messages"]) == 1
    msg = out["messages"][0]
    assert msg["id"] == 10
    assert msg["route"] == "notifications"
    assert msg["handling_status"] == "new"
    assert msg["text_snapshot"] == "привет, когда дайджест?"
    # deeplink на диалог сообщества для ответа в VK
    assert msg["dialog_url"] == "https://vk.com/gim100?sel=42"


async def test_community_dm_inbox_passes_limit_and_include_handled():
    """Smoke: include_handled и limit не ломают сборку запроса."""
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalars_all([]))
    out = await api.get_community_dm_inbox(include_handled=True, limit=50, db=db)
    assert out["messages"] == []
    db.execute.assert_awaited_once()
