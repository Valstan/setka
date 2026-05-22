"""Base class for VK notification checkers.

Centralises three pieces of logic that every VK checker needs:

1. Two-token routing — community access token (preferred) with user-token
   fallback. Lazy-cached vk_api handle per community-id so we don't recreate
   sessions on every call.
2. Automatic retry on community-token errors that signal missing `manage`
   scope (VK error codes 15 and 27). Community access tokens issued via
   `vk.com/club{ID} → Управление → Работа с API` typically don't carry
   `manage`, so wall.* methods need to be retried via the admin user-token.
3. Uniform structured logging of which token actually fulfilled the call
   (`community-token` / `user-token` / `community-fallback-user`).

Before this base class each checker had its own copy of __init__, _api_for
and (in comments_checker) _call_with_fallback. Three places to keep in sync
is two too many — see DEV_HISTORY 2026-05-21 "Hot-fix VK community-tokens".
"""

from __future__ import annotations

import logging
from typing import Callable, Dict, Optional

import vk_api
from vk_api.exceptions import ApiError

logger = logging.getLogger(__name__)


# VK API error codes that mean "this token cannot perform this method on this
# community". Almost always seen with community access tokens that were issued
# without the `manage` scope; user-token (admin of the group) usually works.
COMMUNITY_FALLBACK_CODES: frozenset = frozenset({15, 27})


class BaseVKChecker:
    """Common scaffolding for VK API checkers.

    Subclasses use `self.vk` for the user-token API handle and
    `self._call_with_fallback(group_id, op_name, fn)` to make wall/comments
    calls with automatic community-token → user-token retry.
    """

    # Subclasses override for diagnostic logs.
    CHECKER_NAME: str = "BaseVKChecker"

    def __init__(
        self,
        vk_token: str,
        community_tokens: Optional[Dict[int, str]] = None,
    ) -> None:
        try:
            self.session = vk_api.VkApi(token=vk_token)
            self.vk = self.session.get_api()
            self.community_tokens: Dict[int, str] = dict(community_tokens or {})
            # Lazy cache: community_id → vk_api handle.
            # Avoids re-creating VkApi() on every per-group call.
            self._community_apis: Dict[int, object] = {}
            logger.info(
                "%s initialized (community tokens: %d)",
                self.CHECKER_NAME,
                len(self.community_tokens),
            )
        except Exception as e:
            logger.error("Failed to initialize %s: %s", self.CHECKER_NAME, e)
            raise

    def _api_for(self, group_id: int):
        """Return (vk_api_handle, via_community) for the given group.

        If a community access token is configured for `abs(group_id)`, returns
        a cached handle bound to it; otherwise the shared user-token handle.
        """
        cid = abs(int(group_id))
        tok = self.community_tokens.get(cid)
        if not tok:
            return self.vk, False
        api = self._community_apis.get(cid)
        if api is None:
            api = vk_api.VkApi(token=tok).get_api()
            self._community_apis[cid] = api
        return api, True

    def _call_with_fallback(
        self,
        group_id: int,
        op_name: str,
        fn: Callable,
        *,
        fallback_codes: frozenset = COMMUNITY_FALLBACK_CODES,
    ):
        """Execute `fn(api)` via the community-token handle if available; on
        ApiError with `code in fallback_codes` retry via the user-token handle.

        Returns:
            (response, via_label) — where via_label is one of
            'community-token', 'user-token', or 'community-fallback-user'.
            Raises the final ApiError if the user-token retry also fails or
            if the first-try error is not in `fallback_codes`.
        """
        api, via_community = self._api_for(group_id)
        try:
            return fn(api), ("community-token" if via_community else "user-token")
        except ApiError as e:
            if via_community and e.code in fallback_codes:
                logger.info(
                    "%s group %s: community-token failed on %s with code %s, "
                    "retrying via user-token",
                    self.CHECKER_NAME,
                    group_id,
                    op_name,
                    e.code,
                )
                return fn(self.vk), "community-fallback-user"
            raise
