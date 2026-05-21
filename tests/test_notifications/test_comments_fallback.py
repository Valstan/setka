"""Tests for community-token → user-token fallback in VKCommentsChecker.

Also covers the historical bug where `_get_recent_wall_posts_with_comments`
used `self.vk` directly instead of `_api_for(owner_id)`, bypassing
community-tokens entirely.
"""
from unittest.mock import MagicMock, patch

from vk_api.exceptions import ApiError

from modules.notifications.vk_comments_checker import VKCommentsChecker


OWNER_ID = -158787639
POST_ID = 1234
CUTOFF = 1716200000
COMMUNITY_TOKEN = "vk1.community.fake"
USER_TOKEN = "vk1.user.fake"


def _api_error(code: int) -> ApiError:
    return ApiError(
        vk=None,
        method="wall.getComments",
        values={},
        raw=None,
        error={"error_code": code, "error_msg": f"Fake error {code}"},
    )


def _build_checker_with_community_token():
    with patch("modules.notifications.base_checker.vk_api.VkApi") as m:
        instance = MagicMock()
        instance.get_api.return_value = MagicMock(name="user-api")
        m.return_value = instance
        checker = VKCommentsChecker(
            USER_TOKEN, community_tokens={abs(OWNER_ID): COMMUNITY_TOKEN}
        )
    return checker


def test_comments_fallback_on_code_27():
    checker = _build_checker_with_community_token()
    community_api = MagicMock()
    community_api.wall.getComments.side_effect = _api_error(27)
    checker.vk.wall.getComments.return_value = {
        "items": [
            {"id": 1, "date": CUTOFF + 10, "text": "ok"},
            {"id": 2, "date": CUTOFF - 999, "text": "too-old"},
        ]
    }

    with patch("modules.notifications.base_checker.vk_api.VkApi") as m:
        community_instance = MagicMock()
        community_instance.get_api.return_value = community_api
        m.return_value = community_instance
        result = checker.check_post_comments_since(OWNER_ID, POST_ID, CUTOFF)

    assert len(result) == 1
    assert result[0]["id"] == 1
    checker.vk.wall.getComments.assert_called_once()


def test_wall_get_uses_community_token_not_self_vk():
    """Regression: _get_recent_wall_posts_with_comments must route through _api_for,
    not call self.vk directly (was the bug before the fix).
    """
    checker = _build_checker_with_community_token()
    community_api = MagicMock()
    community_api.wall.get.return_value = {
        "items": [
            {"id": 100, "date": CUTOFF + 100, "comments": {"count": 2}},
        ]
    }

    with patch("modules.notifications.base_checker.vk_api.VkApi") as m:
        community_instance = MagicMock()
        community_instance.get_api.return_value = community_api
        m.return_value = community_instance
        posts = checker._get_recent_wall_posts_with_comments(
            owner_id=OWNER_ID, cutoff_ts=CUTOFF
        )

    assert len(posts) == 1
    assert posts[0]["id"] == 100
    # Community-API was called for wall.get — proves we routed through _api_for.
    community_api.wall.get.assert_called_once()
    # User-token was NOT used (no fallback was needed).
    checker.vk.wall.get.assert_not_called()


def test_wall_get_fallback_on_27():
    """If community-token wall.get returns 27, retry via user-token."""
    checker = _build_checker_with_community_token()
    community_api = MagicMock()
    community_api.wall.get.side_effect = _api_error(27)
    checker.vk.wall.get.return_value = {
        "items": [{"id": 200, "date": CUTOFF + 50, "comments": {"count": 1}}]
    }

    with patch("modules.notifications.base_checker.vk_api.VkApi") as m:
        community_instance = MagicMock()
        community_instance.get_api.return_value = community_api
        m.return_value = community_instance
        posts = checker._get_recent_wall_posts_with_comments(
            owner_id=OWNER_ID, cutoff_ts=CUTOFF
        )

    assert len(posts) == 1
    assert posts[0]["id"] == 200
    checker.vk.wall.get.assert_called_once()


def test_unrelated_error_not_retried_comments():
    checker = _build_checker_with_community_token()
    community_api = MagicMock()
    community_api.wall.getComments.side_effect = _api_error(100)

    with patch("modules.notifications.base_checker.vk_api.VkApi") as m:
        community_instance = MagicMock()
        community_instance.get_api.return_value = community_api
        m.return_value = community_instance
        result = checker.check_post_comments_since(OWNER_ID, POST_ID, CUTOFF)

    assert result == []
    checker.vk.wall.getComments.assert_not_called()
