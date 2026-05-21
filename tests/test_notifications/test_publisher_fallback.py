"""Tests for community-token → publish-token fallback in VKPublisher._call_wall_post.

Mirrors the suggested/comments fallback: when wall.post or wall.repost via a
community client fails with VK code 15 or 27, the publisher should retry via
its own publish-token client. Other errors propagate.
"""
from unittest.mock import MagicMock


def _client_with_method_only(method_return):
    """Make a client that has `method` but NOT `api_call`.

    The publisher checks `hasattr(client, 'api_call')` first; on a bare
    MagicMock both attributes exist, so we use spec to constrain available
    attributes to just `method`.
    """
    client = MagicMock(spec=["method"])
    client.method.return_value = method_return
    return client

import pytest

from modules.publisher.vk_publisher_extended import VKPublisher


def _make_publisher(publish_client):
    """Build a VKPublisher bypassing __init__ network."""
    publisher = VKPublisher.__new__(VKPublisher)
    publisher.vk_client = publish_client
    publisher.test_polygon_mode = False
    publisher.test_polygon_group_id = -137760500
    publisher._last_post_time = {}
    publisher._community_tokens = {}
    publisher._community_clients = {}
    return publisher


def _vk_error_response(code: int, msg: str = "Fake"):
    return {"error": {"error_code": code, "error_msg": msg}}


@pytest.mark.asyncio
async def test_repost_fallback_on_27():
    publish_client = _client_with_method_only({"response": {"success": 1, "post_id": 999}})
    community_client = _client_with_method_only(_vk_error_response(27, "Group auth failed"))

    publisher = _make_publisher(publish_client)
    response = await publisher._call_wall_post(
        params={"object": "wall-1_2"},
        method="wall.repost",
        client=community_client,
    )

    publish_client.method.assert_called_once_with("wall.repost", {"object": "wall-1_2"})
    community_client.method.assert_called_once()
    assert response == {"success": 1, "post_id": 999}


@pytest.mark.asyncio
async def test_post_fallback_on_15():
    publish_client = _client_with_method_only({"response": {"post_id": 555}})
    community_client = _client_with_method_only(_vk_error_response(15, "Access denied"))

    publisher = _make_publisher(publish_client)
    response = await publisher._call_wall_post(
        params={"owner_id": -1, "message": "x"},
        method="wall.post",
        client=community_client,
    )

    publish_client.method.assert_called_once()
    assert response == {"post_id": 555}


@pytest.mark.asyncio
async def test_no_fallback_when_already_publish_client():
    """If the call was already routed through publish-client, no fallback retry."""
    publish_client = _client_with_method_only(_vk_error_response(27, "still failing"))
    publisher = _make_publisher(publish_client)

    with pytest.raises(Exception) as excinfo:
        await publisher._call_wall_post(
            params={"owner_id": -1, "message": "x"},
            method="wall.post",
            client=publish_client,
        )

    assert "still failing" in str(excinfo.value)
    publish_client.method.assert_called_once()


@pytest.mark.asyncio
async def test_unrelated_error_propagates():
    """VK errors outside the fallback set must bubble up without retry."""
    publish_client = _client_with_method_only({"response": {"post_id": 1}})
    community_client = _client_with_method_only(_vk_error_response(100, "validation failed"))

    publisher = _make_publisher(publish_client)

    with pytest.raises(Exception):
        await publisher._call_wall_post(
            params={"owner_id": -1, "message": "x"},
            method="wall.post",
            client=community_client,
        )

    community_client.method.assert_called_once()
    publish_client.method.assert_not_called()


@pytest.mark.asyncio
async def test_community_token_success_no_fallback():
    """Happy path — community-token works first try, publish-client untouched."""
    publish_client = _client_with_method_only({"response": {"post_id": 1}})
    community_client = _client_with_method_only({"response": {"post_id": 42}})

    publisher = _make_publisher(publish_client)
    response = await publisher._call_wall_post(
        params={"owner_id": -1, "message": "ok"},
        method="wall.post",
        client=community_client,
    )

    community_client.method.assert_called_once()
    publish_client.method.assert_not_called()
    assert response == {"post_id": 42}
