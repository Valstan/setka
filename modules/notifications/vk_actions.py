"""VK write-actions for the notifications UI (etap 4a).

Currently implemented:
- like_comment(owner_id, post_id, comment_id) — add a like from a group
  (preferred via community-token; if absent, user-token).

Planned for etap 4b (kept as stubs / not exported yet):
- reply_to_comment(...): wall.createComment(reply_to_comment=...)
- send_message(...):     messages.send(...)
"""
from __future__ import annotations

import logging
from typing import Dict, Optional

import vk_api
from vk_api.exceptions import ApiError

logger = logging.getLogger(__name__)


COMMUNITY_FALLBACK_CODES: frozenset = frozenset({15, 27})


def _api_for(owner_id: int, *, user_token: str, community_tokens: Dict[int, str]):
    """Return (api_handle, via_community) for the target wall owner."""
    cid = abs(int(owner_id))
    tok = community_tokens.get(cid)
    if tok:
        return vk_api.VkApi(token=tok).get_api(), True
    return vk_api.VkApi(token=user_token).get_api(), False


def _call_with_fallback(
    *,
    owner_id: int,
    op_name: str,
    fn,
    user_token: str,
    community_tokens: Dict[int, str],
):
    api, via_community = _api_for(
        owner_id, user_token=user_token, community_tokens=community_tokens,
    )
    try:
        return fn(api), ("community-token" if via_community else "user-token")
    except ApiError as e:
        if via_community and e.code in COMMUNITY_FALLBACK_CODES:
            logger.info(
                "VK action %s for owner %s failed via community-token with code %s, "
                "retrying via user-token",
                op_name, owner_id, e.code,
            )
            api2 = vk_api.VkApi(token=user_token).get_api()
            return fn(api2), "community-fallback-user"
        raise


def like_comment(
    *,
    owner_id: int,
    post_id: int,
    comment_id: int,
    user_token: str,
    community_tokens: Optional[Dict[int, str]] = None,
) -> Dict:
    """Like a comment under wall{owner_id}_{post_id} on behalf of the group.

    VK API: `likes.add(type='comment', owner_id=..., item_id=comment_id)`.
    Idempotent — VK keeps a single like per (user, target).

    Returns:
        {success, likes_count, via} on success.
        {success: False, error_code, error} on failure.
    """
    community_tokens = community_tokens or {}

    def call(api):
        return api.likes.add(
            type='comment',
            owner_id=owner_id,
            item_id=comment_id,
        )

    try:
        resp, via = _call_with_fallback(
            owner_id=owner_id,
            op_name='likes.add(comment)',
            fn=call,
            user_token=user_token,
            community_tokens=community_tokens,
        )
        likes = int((resp or {}).get('likes', 0))
        logger.info(
            "✅ Liked comment wall%s_%s (cid=%s) — total likes: %d (via %s)",
            owner_id, post_id, comment_id, likes, via,
        )
        return {'success': True, 'likes_count': likes, 'via': via}
    except ApiError as e:
        logger.warning(
            "Failed to like comment wall%s_%s (cid=%s): [%s] %s",
            owner_id, post_id, comment_id, e.code, e,
        )
        return {'success': False, 'error_code': e.code, 'error': str(e)}
    except Exception as e:
        logger.error("Unexpected error liking comment: %s", e)
        return {'success': False, 'error_code': 0, 'error': str(e)}
