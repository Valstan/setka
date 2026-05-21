"""Tests for VK write-actions (etap 4a): like_comment."""
from unittest.mock import MagicMock, patch

from vk_api.exceptions import ApiError

from modules.notifications.vk_actions import like_comment


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
