"""Tests for BaseVKChecker (shared scaffolding for the three VK checkers)."""
from unittest.mock import MagicMock, patch

from vk_api.exceptions import ApiError

from modules.notifications.base_checker import (
    BaseVKChecker,
    COMMUNITY_FALLBACK_CODES,
)


GROUP = -123
COMMUNITY_TOKEN = "vk1.community.fake"
USER_TOKEN = "vk1.user.fake"


def _api_error(code: int) -> ApiError:
    return ApiError(
        vk=None, method="wall.get", values={}, raw=None,
        error={"error_code": code, "error_msg": f"err {code}"},
    )


def _make_checker(with_community: bool = True):
    """Construct a BaseVKChecker through __init__ so it really exercises the
    code path that builds session+user-api."""
    community_tokens = {abs(GROUP): COMMUNITY_TOKEN} if with_community else {}
    with patch("modules.notifications.base_checker.vk_api.VkApi") as m:
        m.return_value.get_api.return_value = MagicMock(name="user-api")
        checker = BaseVKChecker(USER_TOKEN, community_tokens=community_tokens)
    return checker


def test_api_for_returns_user_api_when_no_community_token():
    checker = _make_checker(with_community=False)
    api, via = checker._api_for(GROUP)
    assert via is False
    assert api is checker.vk


def test_api_for_returns_community_api_and_caches_it():
    checker = _make_checker()
    with patch("modules.notifications.base_checker.vk_api.VkApi") as m:
        community_api = MagicMock(name="community-api")
        m.return_value.get_api.return_value = community_api
        api1, via1 = checker._api_for(GROUP)
        api2, via2 = checker._api_for(GROUP)
    assert via1 is True and via2 is True
    assert api1 is community_api
    # Second call must NOT spin up a new VkApi instance (cache hit).
    assert m.call_count == 1
    assert api1 is api2


def test_call_with_fallback_success_via_community():
    checker = _make_checker()
    with patch("modules.notifications.base_checker.vk_api.VkApi") as m:
        community_api = MagicMock(name="community-api")
        m.return_value.get_api.return_value = community_api

        resp, via = checker._call_with_fallback(
            GROUP, "wall.get", lambda api: {"ok": True, "via": api}
        )
    assert via == "community-token"
    assert resp["via"] is community_api


def test_call_with_fallback_retries_on_27():
    checker = _make_checker()
    user_api = checker.vk
    user_api.test_marker = "user"
    with patch("modules.notifications.base_checker.vk_api.VkApi") as m:
        community_api = MagicMock(name="community-api")
        m.return_value.get_api.return_value = community_api

        attempts = {"i": 0}

        def call(api):
            attempts["i"] += 1
            if api is community_api:
                raise _api_error(27)
            return {"ok": True, "via": api}

        resp, via = checker._call_with_fallback(GROUP, "wall.get", call)

    assert via == "community-fallback-user"
    assert resp["via"] is user_api
    assert attempts["i"] == 2  # community first, user retry


def test_call_with_fallback_no_retry_on_other_codes():
    checker = _make_checker()
    with patch("modules.notifications.base_checker.vk_api.VkApi") as m:
        community_api = MagicMock(name="community-api")
        m.return_value.get_api.return_value = community_api

        def call(api):
            if api is community_api:
                raise _api_error(100)
            return {"ok": True}

        try:
            checker._call_with_fallback(GROUP, "wall.get", call)
        except ApiError as e:
            assert e.code == 100
        else:
            raise AssertionError("Expected ApiError to propagate")


def test_call_with_fallback_no_community_token_propagates_error():
    checker = _make_checker(with_community=False)

    def call(api):
        raise _api_error(27)  # would have triggered fallback if community

    try:
        checker._call_with_fallback(GROUP, "wall.get", call)
    except ApiError as e:
        assert e.code == 27
    else:
        raise AssertionError("Expected ApiError to propagate")


def test_fallback_codes_constant_includes_15_and_27():
    assert 15 in COMMUNITY_FALLBACK_CODES
    assert 27 in COMMUNITY_FALLBACK_CODES
