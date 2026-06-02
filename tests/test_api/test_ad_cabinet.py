"""Тесты API рекламного кабинета (web/api/ad_cabinet).

Сессия БД мокается (AsyncMock), VK-маршрутизация/отправка monkeypatch'атся —
без реальной БД и VK, в стиле tests/test_api/test_communities_changed_category.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

import modules.notifications.vk_actions as vk_actions
import modules.vk_token_router as token_router
from database.models import AdRequest, MessageTemplate
from web.api import ad_cabinet as api


def _scalars_all(objs):
    r = MagicMock()
    r.scalars.return_value.all.return_value = objs
    return r


def _ad_request(**kw):
    defaults = dict(
        id=1,
        region_id=7,
        community_vk_id=-100,
        community_name="Малмыж Инфо",
        vk_post_id=5,
        author_vk_id=42,
        signer_id=None,
        peer_id=42,
        author_name="Иван",
        author_is_group=False,
        status="new",
        prepared_message="Здравствуйте, Иван!",
    )
    defaults.update(kw)
    return AdRequest(**defaults)


# ---------------------------------------------------------------- list


async def test_list_requests_serializes():
    ar = _ad_request()
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalars_all([ar]))
    out = await api.list_requests(db=db)
    assert len(out["requests"]) == 1
    assert out["requests"][0]["id"] == 1
    assert out["requests"][0]["vk_post_url"] == "https://vk.com/wall-100_5"


# ---------------------------------------------------------------- prepare


async def test_prepare_renders_and_saves():
    ar = _ad_request(author_name="Пётр", prepared_message=None)
    tpl = MessageTemplate(
        id=3,
        title="Оффер",
        body="Здравствуйте, {author_name}! Пишу из «{community_name}».",
        category="ad_offer",
    )
    db = AsyncMock()
    db.get = AsyncMock(side_effect=[ar, tpl])
    out = await api.prepare_reply(1, api.PrepareIn(template_id=3), db=db)
    assert "Пётр" in out["prepared_message"]
    assert "Малмыж Инфо" in out["prepared_message"]
    assert ar.prepared_message == out["prepared_message"]
    assert ar.template_id == 3


async def test_prepare_404_unknown_request():
    db = AsyncMock()
    db.get = AsyncMock(return_value=None)
    with pytest.raises(HTTPException) as exc:
        await api.prepare_reply(999, api.PrepareIn(template_id=3), db=db)
    assert exc.value.status_code == 404


# ---------------------------------------------------------------- send


async def test_send_not_allowed_returns_deeplink(monkeypatch):
    ar = _ad_request()
    db = AsyncMock()
    db.get = AsyncMock(return_value=ar)
    monkeypatch.setattr(token_router, "load_vk_routing", AsyncMock(return_value=("utok", {})))
    monkeypatch.setattr(vk_actions, "messages_allowed", MagicMock(return_value=False))
    monkeypatch.setattr(vk_actions, "send_message", MagicMock())

    out = await api.send_reply(1, db=db)
    assert out["allowed"] is False
    assert out["personal_deeplink"] == "https://vk.com/im?sel=42"
    assert ar.status == "new"  # статус не меняем — модератор подтвердит вручную
    vk_actions.send_message.assert_not_called()


async def test_send_allowed_sends_and_marks_contacted(monkeypatch):
    ar = _ad_request()
    db = AsyncMock()
    db.get = AsyncMock(return_value=ar)
    monkeypatch.setattr(token_router, "load_vk_routing", AsyncMock(return_value=("utok", {})))
    monkeypatch.setattr(vk_actions, "messages_allowed", MagicMock(return_value=True))
    monkeypatch.setattr(
        vk_actions,
        "send_message",
        MagicMock(return_value={"success": True, "message_id": 555, "via": "community-token"}),
    )

    out = await api.send_reply(1, db=db)
    assert out["success"] is True
    assert out["vk_message_id"] == 555
    assert ar.status == "contacted"
    assert ar.vk_message_id == 555
    assert ar.contacted_at is not None


async def test_send_901_fallback(monkeypatch):
    ar = _ad_request()
    db = AsyncMock()
    db.get = AsyncMock(return_value=ar)
    monkeypatch.setattr(token_router, "load_vk_routing", AsyncMock(return_value=("utok", {})))
    # precheck неизвестен → пробуем отправить, VK вернёт 901.
    monkeypatch.setattr(vk_actions, "messages_allowed", MagicMock(return_value=None))
    monkeypatch.setattr(
        vk_actions,
        "send_message",
        MagicMock(
            return_value={
                "success": False,
                "error_code": 901,
                "allowed": False,
                "personal_deeplink": "https://vk.com/im?sel=42",
            }
        ),
    )

    out = await api.send_reply(1, db=db)
    assert out["allowed"] is False
    assert out["personal_deeplink"] == "https://vk.com/im?sel=42"
    assert ar.status == "new"


async def test_send_author_group_blocked():
    ar = _ad_request(author_is_group=True, peer_id=-200)
    db = AsyncMock()
    db.get = AsyncMock(return_value=ar)
    out = await api.send_reply(1, db=db)
    assert out["allowed"] is False
    assert out["reason"] == "author_is_group"


async def test_send_requires_prepared_message():
    ar = _ad_request(prepared_message=None)
    db = AsyncMock()
    db.get = AsyncMock(return_value=ar)
    with pytest.raises(HTTPException) as exc:
        await api.send_reply(1, db=db)
    assert exc.value.status_code == 400


# ---------------------------------------------------------------- status


async def test_set_status_invalid():
    db = AsyncMock()
    with pytest.raises(HTTPException) as exc:
        await api.set_status(1, api.StatusIn(status="bogus"), db=db)
    assert exc.value.status_code == 400


async def test_set_status_published():
    ar = _ad_request()
    db = AsyncMock()
    db.get = AsyncMock(return_value=ar)
    out = await api.set_status(1, api.StatusIn(status="published"), db=db)
    assert out["status"] == "published"
    assert ar.status == "published"
