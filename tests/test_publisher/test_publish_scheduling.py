"""Тесты seam'а планировщика: publish_date (отложка), signed, комментарии.

Покрывает аддитивные параметры :meth:`VKPublisher.publish_digest` и метод
:meth:`VKPublisher.set_post_comments`. Гарантия — обычные дайджесты
(без ``publish_date``/``signed``) не меняют поведение (zero regression).
"""

from __future__ import annotations

import pytest

from modules.publisher.vk_publisher_extended import VKPublisher


class _DummyVkClient:
    def __init__(self):
        self.calls = []

    def api_call(self, method, params):
        self.calls.append((method, params))
        if method in ("wall.openComments", "wall.closeComments"):
            return {"response": 1}
        return {"response": {"post_id": 555}}


@pytest.mark.asyncio
async def test_publish_digest_without_publish_date_is_immediate():
    """Дайджест без publish_date → нет параметра, postponed=False (регресс-гард)."""
    vk = _DummyVkClient()
    publisher = VKPublisher(vk_client=vk)

    result = await publisher.publish_digest(group_id=-100, text="digest")

    assert result["success"] is True
    assert result["postponed"] is False
    _method, params = vk.calls[0]
    assert "publish_date" not in params
    assert "signed" not in params


@pytest.mark.asyncio
async def test_publish_digest_with_publish_date_schedules_postponed():
    vk = _DummyVkClient()
    publisher = VKPublisher(vk_client=vk)

    result = await publisher.publish_digest(group_id=-100, text="ad post", publish_date=1900000000)

    assert result["success"] is True
    assert result["postponed"] is True
    method, params = vk.calls[0]
    assert method == "wall.post"
    assert params["publish_date"] == 1900000000


@pytest.mark.asyncio
async def test_publish_date_zero_is_treated_as_immediate():
    """publish_date=0 (или None) — не отложка, параметр не уходит."""
    vk = _DummyVkClient()
    publisher = VKPublisher(vk_client=vk)

    result = await publisher.publish_digest(group_id=-100, text="x", publish_date=0)

    assert result["postponed"] is False
    _method, params = vk.calls[0]
    assert "publish_date" not in params


@pytest.mark.asyncio
async def test_signed_param_adds_author_signature():
    vk = _DummyVkClient()
    publisher = VKPublisher(vk_client=vk)

    await publisher.publish_digest(group_id=-100, text="x", signed=True)

    _method, params = vk.calls[0]
    assert params["signed"] == 1


@pytest.mark.asyncio
async def test_set_post_comments_closed_calls_close_comments():
    vk = _DummyVkClient()
    publisher = VKPublisher(vk_client=vk)

    result = await publisher.set_post_comments(12345, 99, enabled=False)

    assert result["success"] is True
    method, params = vk.calls[0]
    assert method == "wall.closeComments"
    assert params["owner_id"] == -12345  # normalized to negative owner
    assert params["post_id"] == 99


@pytest.mark.asyncio
async def test_set_post_comments_open_calls_open_comments():
    vk = _DummyVkClient()
    publisher = VKPublisher(vk_client=vk)

    result = await publisher.set_post_comments(-12345, 77, enabled=True)

    assert result["success"] is True
    method, params = vk.calls[0]
    assert method == "wall.openComments"
    assert params["owner_id"] == -12345
    assert params["post_id"] == 77
