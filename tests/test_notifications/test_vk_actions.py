"""Tests for VK write-actions (etaps 4a + 4b): like / reply / send_message."""
from unittest.mock import MagicMock, patch

from vk_api.exceptions import ApiError

from modules.notifications.vk_actions import (
    like_comment,
    reply_to_comment,
    send_message,
)


OWNER = -123
POST = 42
CID = 100


def _api_error(code: int) -> ApiError:
    return ApiError(
        vk=None, method="likes.add", values={}, raw=None,
        error={"error_code": code, "error_msg": f"err {code}"},
    )


def test_like_comment_via_community_token():
    """Happy path: community-token works."""
    with patch("modules.notifications.vk_actions.vk_api.VkApi") as m:
        community_api = MagicMock()
        community_api.likes.add.return_value = {"likes": 42}
        m.return_value.get_api.return_value = community_api

        result = like_comment(
            owner_id=OWNER, post_id=POST, comment_id=CID,
            user_token="user", community_tokens={abs(OWNER): "community"},
        )

    assert result["success"] is True
    assert result["likes_count"] == 42
    assert result["via"] == "community-token"
    community_api.likes.add.assert_called_once_with(
        type="comment", owner_id=OWNER, item_id=CID,
    )


def test_like_comment_fallback_on_27():
    """Community-token fails with 27 → retry via user-token."""
    with patch("modules.notifications.vk_actions.vk_api.VkApi") as m:
        community_api = MagicMock()
        community_api.likes.add.side_effect = _api_error(27)
        user_api = MagicMock()
        user_api.likes.add.return_value = {"likes": 1}
        m.return_value.get_api.side_effect = [community_api, user_api]

        result = like_comment(
            owner_id=OWNER, post_id=POST, comment_id=CID,
            user_token="user", community_tokens={abs(OWNER): "community"},
        )

    assert result["success"] is True
    assert result["via"] == "community-fallback-user"
    user_api.likes.add.assert_called_once()


def test_like_comment_no_community_token():
    """No community-token configured → straight to user-token."""
    with patch("modules.notifications.vk_actions.vk_api.VkApi") as m:
        user_api = MagicMock()
        user_api.likes.add.return_value = {"likes": 5}
        m.return_value.get_api.return_value = user_api

        result = like_comment(
            owner_id=OWNER, post_id=POST, comment_id=CID,
            user_token="user", community_tokens={},
        )

    assert result["success"] is True
    assert result["via"] == "user-token"


def test_like_comment_unrelated_error_returns_failure():
    """Error outside fallback set → failure payload, no retry."""
    with patch("modules.notifications.vk_actions.vk_api.VkApi") as m:
        community_api = MagicMock()
        community_api.likes.add.side_effect = _api_error(100)
        m.return_value.get_api.return_value = community_api

        result = like_comment(
            owner_id=OWNER, post_id=POST, comment_id=CID,
            user_token="user", community_tokens={abs(OWNER): "community"},
        )

    assert result["success"] is False
    assert result["error_code"] == 100


# ─────────────────────────────────────────────────────────────────
# Etap 4b: reply_to_comment
# ─────────────────────────────────────────────────────────────────

def test_reply_to_comment_via_community_token():
    """Happy path: community-token works. from_group must be positive."""
    with patch("modules.notifications.vk_actions.vk_api.VkApi") as m:
        community_api = MagicMock()
        community_api.wall.createComment.return_value = {"comment_id": 555}
        m.return_value.get_api.return_value = community_api

        result = reply_to_comment(
            owner_id=OWNER, post_id=POST, comment_id=CID, message="hi",
            user_token="user", community_tokens={abs(OWNER): "community"},
        )

    assert result["success"] is True
    assert result["comment_id"] == 555
    assert result["via"] == "community-token"
    community_api.wall.createComment.assert_called_once_with(
        owner_id=OWNER, post_id=POST, message="hi",
        reply_to_comment=CID, from_group=abs(OWNER),
    )


def test_reply_to_comment_fallback_on_15():
    """Code 15 → retry via user-token, via='community-fallback-user'."""
    with patch("modules.notifications.vk_actions.vk_api.VkApi") as m:
        community_api = MagicMock()
        community_api.wall.createComment.side_effect = _api_error(15)
        user_api = MagicMock()
        user_api.wall.createComment.return_value = {"comment_id": 777}
        m.return_value.get_api.side_effect = [community_api, user_api]

        result = reply_to_comment(
            owner_id=OWNER, post_id=POST, comment_id=CID, message="hi",
            user_token="user", community_tokens={abs(OWNER): "community"},
        )

    assert result["success"] is True
    assert result["via"] == "community-fallback-user"
    user_api.wall.createComment.assert_called_once()


def test_reply_to_comment_empty_message_rejected_without_api_call():
    """Empty/whitespace message → failure without any VK call."""
    with patch("modules.notifications.vk_actions.vk_api.VkApi") as m:
        result = reply_to_comment(
            owner_id=OWNER, post_id=POST, comment_id=CID, message="   ",
            user_token="user", community_tokens={},
        )
    assert result["success"] is False
    assert "empty" in result["error"].lower()
    m.assert_not_called()


def test_reply_to_comment_message_trimmed():
    """Leading/trailing whitespace must be stripped before VK call."""
    with patch("modules.notifications.vk_actions.vk_api.VkApi") as m:
        api = MagicMock()
        api.wall.createComment.return_value = {"comment_id": 1}
        m.return_value.get_api.return_value = api

        reply_to_comment(
            owner_id=OWNER, post_id=POST, comment_id=CID,
            message="  hello world  \n",
            user_token="user", community_tokens={},
        )

    args, kwargs = api.wall.createComment.call_args
    assert kwargs["message"] == "hello world"


# ─────────────────────────────────────────────────────────────────
# Etap 4b: send_message
# ─────────────────────────────────────────────────────────────────

def test_send_message_via_community_token():
    """Happy path: community-token, returns message_id."""
    GROUP = 158787639
    PEER = 12345
    with patch("modules.notifications.vk_actions.vk_api.VkApi") as m:
        community_api = MagicMock()
        # messages.send returns an int (the new message_id) directly
        community_api.messages.send.return_value = 999
        m.return_value.get_api.return_value = community_api

        result = send_message(
            group_id=GROUP, peer_id=PEER, message="привет",
            user_token="user", community_tokens={GROUP: "community"},
            random_id=42,
        )

    assert result["success"] is True
    assert result["message_id"] == 999
    assert result["via"] == "community-token"
    community_api.messages.send.assert_called_once_with(
        peer_id=PEER, message="привет",
        random_id=42, group_id=GROUP,
    )


def test_send_message_handles_negative_group_id():
    """Caller can pass either positive or negative group_id; we abs() it."""
    PEER = 12345
    with patch("modules.notifications.vk_actions.vk_api.VkApi") as m:
        community_api = MagicMock()
        community_api.messages.send.return_value = 1
        m.return_value.get_api.return_value = community_api

        send_message(
            group_id=-158787639, peer_id=PEER, message="x",
            user_token="user", community_tokens={158787639: "community"},
            random_id=7,
        )

    _args, kwargs = community_api.messages.send.call_args
    assert kwargs["group_id"] == 158787639


def test_send_message_empty_message_rejected():
    with patch("modules.notifications.vk_actions.vk_api.VkApi") as m:
        result = send_message(
            group_id=158787639, peer_id=1, message="",
            user_token="user", community_tokens={},
        )
    assert result["success"] is False
    m.assert_not_called()


def test_send_message_random_id_autogenerated_when_missing():
    """If caller omits random_id we must still produce a valid call."""
    with patch("modules.notifications.vk_actions.vk_api.VkApi") as m:
        api = MagicMock()
        api.messages.send.return_value = 1
        m.return_value.get_api.return_value = api

        send_message(
            group_id=158787639, peer_id=1, message="hi",
            user_token="user", community_tokens={},
        )

    _args, kwargs = api.messages.send.call_args
    assert isinstance(kwargs["random_id"], int)
    assert kwargs["random_id"] > 0
