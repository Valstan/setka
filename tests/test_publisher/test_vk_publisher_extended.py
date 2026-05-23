"""Unit tests for VKPublisher group ID normalization."""

import os
import sys

import pytest

# Ensure project root is importable when pytest runs outside configured PYTHONPATH.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from modules.publisher.vk_publisher_extended import VKPublisher  # noqa: E402


class _DummyVkClient:
    def __init__(self):
        self.calls = []

    def api_call(self, method, params):
        self.calls.append((method, params))
        if method == "wall.repost":
            return {"response": {"success": 1, "post_id": 777}}
        return {"response": {"post_id": 555}}


def test_normalize_group_owner_id():
    assert VKPublisher._normalize_group_owner_id(-12345) == -12345
    assert VKPublisher._normalize_group_owner_id(12345) == -12345


@pytest.mark.asyncio
async def test_publish_digest_normalizes_positive_group_id():
    vk = _DummyVkClient()
    publisher = VKPublisher(vk_client=vk)

    result = await publisher.publish_digest(
        group_id=12345,
        text="digest text",
        attachments=[],
    )

    assert result["success"] is True
    assert vk.calls, "Expected VK API call to be made"
    method, params = vk.calls[0]
    assert method == "wall.post"
    assert params["owner_id"] == -12345


@pytest.mark.asyncio
async def test_publish_repost_normalizes_positive_group_id_and_sets_group_id():
    vk = _DummyVkClient()
    publisher = VKPublisher(vk_client=vk)

    result = await publisher.publish_repost(
        group_id=12345,
        source_owner_id=-111,
        source_post_id=222,
    )

    assert result["success"] is True
    assert vk.calls, "Expected VK API call to be made"
    method, params = vk.calls[0]
    assert method == "wall.repost"
    assert params["group_id"] == 12345
    assert params["object"] == "wall-111_222"
