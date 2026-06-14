"""Идемпотентность одно-кнопочных публикаций рекламы (анти-двойной-клик).

publish_request_now (#239, моментальный wall.post) и accept_request (С5, отложка)
не должны постить/слать дважды по уже опубликованной заявке. Здесь — быстрый
unit на early-out «already» без захода в VK/сеть (db — фейк).
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from database.models import AdRequest
from web.api.ad_cabinet import AcceptRequestIn, accept_request, publish_request_now


class _FakeDB:
    """Возвращает одну заявку и для .get, и для select(...).with_for_update()."""

    def __init__(self, ar):
        self._ar = ar
        self.committed = 0

    async def get(self, model, ident):
        return self._ar

    async def execute(self, stmt):
        res = MagicMock()
        res.scalar_one_or_none.return_value = self._ar
        return res

    async def commit(self):
        self.committed += 1


def _published_ar():
    return AdRequest(id=1, status="published", community_vk_id=-100, client_id=42)


def test_publish_now_already_published_is_noop():
    out = asyncio.run(publish_request_now(1, db=_FakeDB(_published_ar())))
    assert out == {"published": True, "already": True}


def test_accept_already_published_is_noop():
    # Не должен ни слать ответ, ни плодить отложку — просто early-out.
    out = asyncio.run(
        accept_request(1, AcceptRequestIn(dates=["2026-06-15T10:00"]), db=_FakeDB(_published_ar()))
    )
    assert out.get("already") is True
    assert out.get("scheduled") == 0
