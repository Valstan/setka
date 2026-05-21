"""Tests for community-token → user-token fallback in VKSuggestedChecker.

VK error code 27 (Group authorization failed) is what we hit in prod when a
community access token was created without `manage` scope. Before the fix the
checker silently returned count=0; now it retries via the user-token.
"""
from unittest.mock import MagicMock, patch

import pytest
from vk_api.exceptions import ApiError

from modules.notifications.vk_suggested_checker import VKSuggestedChecker


GROUP_ID = -158787639  # Малмыж - ИНФО, отрицательный
COMMUNITY_TOKEN = "vk1.community.fake"
USER_TOKEN = "vk1.user.fake"


def _make_api_error(code: int) -> ApiError:
    """ApiError(self, vk, method, values, raw, error) — fake all five."""
    return ApiError(
        vk=None,
        method="wall.get",
        values={},
        raw=None,
        error={"error_code": code, "error_msg": f"Fake error {code}"},
    )


def _build_checker_with_community_token():
    """Patch vk_api.VkApi globally so __init__ doesn't hit the network."""
    with patch("modules.notifications.base_checker.vk_api.VkApi") as m:
        instance = MagicMock()
        instance.get_api.return_value = MagicMock(name="user-api")
        m.return_value = instance
        checker = VKSuggestedChecker(
            USER_TOKEN, community_tokens={abs(GROUP_ID): COMMUNITY_TOKEN}
        )
    return checker


def test_fallback_on_code_27_returns_user_token_result():
    """Community-token raises code 27 → checker retries via user-token (self.vk)."""
    checker = _build_checker_with_community_token()
    community_api = MagicMock(name="community-api")
    community_api.wall.get.side_effect = _make_api_error(27)
    user_api = checker.vk  # already a MagicMock
    user_api.wall.get.return_value = {"count": 3, "items": []}

    with patch("modules.notifications.base_checker.vk_api.VkApi") as m:
        community_instance = MagicMock()
        community_instance.get_api.return_value = community_api
        m.return_value = community_instance
        result = checker.check_suggested_posts(GROUP_ID)

    assert result["count"] == 3
    assert result["has_suggested"] is True
    assert result["via"] == "community-fallback-user"
    user_api.wall.get.assert_called_once()


def test_fallback_on_code_15():
    """Code 15 (Access denied) also triggers fallback."""
    checker = _build_checker_with_community_token()
    community_api = MagicMock()
    community_api.wall.get.side_effect = _make_api_error(15)
    checker.vk.wall.get.return_value = {"count": 1, "items": []}

    with patch("modules.notifications.base_checker.vk_api.VkApi") as m:
        community_instance = MagicMock()
        community_instance.get_api.return_value = community_api
        m.return_value = community_instance
        result = checker.check_suggested_posts(GROUP_ID)

    assert result["count"] == 1
    assert result["via"] == "community-fallback-user"


def test_no_fallback_when_no_community_token():
    """If a group has no community-token, code 27 is fatal (no retry path)."""
    with patch("modules.notifications.base_checker.vk_api.VkApi") as m:
        instance = MagicMock()
        instance.get_api.return_value = MagicMock(name="user-api")
        m.return_value = instance
        checker = VKSuggestedChecker(USER_TOKEN, community_tokens={})

    checker.vk.wall.get.side_effect = _make_api_error(27)
    result = checker.check_suggested_posts(GROUP_ID)

    assert result["count"] == 0
    assert result["has_suggested"] is False
    assert "27" in result["error"]


def test_unrelated_error_not_retried():
    """Code 5 (token invalid) must NOT trigger a community→user retry."""
    checker = _build_checker_with_community_token()
    community_api = MagicMock()
    community_api.wall.get.side_effect = _make_api_error(5)

    with patch("modules.notifications.base_checker.vk_api.VkApi") as m:
        community_instance = MagicMock()
        community_instance.get_api.return_value = community_api
        m.return_value = community_instance
        result = checker.check_suggested_posts(GROUP_ID)

    # user-token NOT called as a fallback
    checker.vk.wall.get.assert_not_called()
    assert result["count"] == 0


def test_community_token_success_no_fallback_needed():
    """Happy path: community-token works, user-token never touched."""
    checker = _build_checker_with_community_token()
    community_api = MagicMock()
    community_api.wall.get.return_value = {"count": 5, "items": []}

    with patch("modules.notifications.base_checker.vk_api.VkApi") as m:
        community_instance = MagicMock()
        community_instance.get_api.return_value = community_api
        m.return_value = community_instance
        result = checker.check_suggested_posts(GROUP_ID)

    assert result["count"] == 5
    assert result["via"] == "community-token"
    checker.vk.wall.get.assert_not_called()
