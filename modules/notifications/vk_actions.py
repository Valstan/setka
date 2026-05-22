"""VK write-actions for the notifications UI (etaps 4a + 4b).

Implemented:
- like_comment(...)    — likes.add(type='comment') from the group account.
- reply_to_comment(...) — wall.createComment(reply_to_comment=..., from_group=1).
- send_message(...)     — messages.send to a conversation, from the group.

All three follow the same two-token routing: try community-token first, fall
back to admin user-token on VK errors 15/27 (community token lacks the needed
scope). The `via` field in the response tells the UI which path actually
succeeded so a future audit can spot regressions.
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


def reply_to_comment(
    *,
    owner_id: int,
    post_id: int,
    comment_id: int,
    message: str,
    user_token: str,
    community_tokens: Optional[Dict[int, str]] = None,
) -> Dict:
    """Reply to a comment under wall{owner_id}_{post_id} as the group.

    VK API: `wall.createComment(owner_id=, post_id=, message=,
                                reply_to_comment=comment_id, from_group=1)`.

    `from_group=1` makes the new comment appear as posted by the community,
    not by the personal admin account. Required for both token paths:
      - community-token: `from_group` is implicit but harmless.
      - user-token: user must be admin/editor of the group; `from_group`
        plus the positive group id makes the post community-attributed.

    Returns:
        {success: True, comment_id, via} on success.
        {success: False, error_code, error} on failure.
    """
    community_tokens = community_tokens or {}
    message = (message or "").strip()
    if not message:
        return {
            'success': False,
            'error_code': 0,
            'error': 'message is empty',
        }
    positive_group_id = abs(int(owner_id))

    def call(api):
        return api.wall.createComment(
            owner_id=owner_id,
            post_id=post_id,
            message=message,
            reply_to_comment=comment_id,
            from_group=positive_group_id,
        )

    try:
        resp, via = _call_with_fallback(
            owner_id=owner_id,
            op_name='wall.createComment',
            fn=call,
            user_token=user_token,
            community_tokens=community_tokens,
        )
        new_cid = int((resp or {}).get('comment_id', 0)) or None
        logger.info(
            "✅ Replied to comment wall%s_%s (parent_cid=%s) → new_cid=%s (via %s)",
            owner_id, post_id, comment_id, new_cid, via,
        )
        return {'success': True, 'comment_id': new_cid, 'via': via}
    except ApiError as e:
        logger.warning(
            "Failed to reply to comment wall%s_%s (parent_cid=%s): [%s] %s",
            owner_id, post_id, comment_id, e.code, e,
        )
        return {'success': False, 'error_code': e.code, 'error': str(e)}
    except Exception as e:
        logger.error("Unexpected error replying to comment: %s", e)
        return {'success': False, 'error_code': 0, 'error': str(e)}


def send_message(
    *,
    group_id: int,
    peer_id: int,
    message: str,
    user_token: str,
    community_tokens: Optional[Dict[int, str]] = None,
    random_id: Optional[int] = None,
) -> Dict:
    """Send a Direct Message to a conversation, from the group account.

    VK API: `messages.send(peer_id, message, random_id, group_id=)`.

    `group_id` is the **positive** id of the community. Required for both
    token paths so VK knows which group is sending.

    `random_id` deduplicates retries on the VK side. Caller may pass a stable
    value (e.g. hash of (peer_id, message)) for idempotent retries; default is
    a one-shot random int.

    Returns:
        {success: True, message_id, via} on success.
        {success: False, error_code, error} on failure.
    """
    import random

    community_tokens = community_tokens or {}
    message = (message or "").strip()
    if not message:
        return {
            'success': False,
            'error_code': 0,
            'error': 'message is empty',
        }
    positive_group_id = abs(int(group_id))
    if random_id is None:
        random_id = random.randint(1, 2**31 - 1)

    def call(api):
        return api.messages.send(
            peer_id=int(peer_id),
            message=message,
            random_id=int(random_id),
            group_id=positive_group_id,
        )

    try:
        # _call_with_fallback expects a signed owner_id to look up the
        # community-token; messages.send works the same way as a wall call here.
        resp, via = _call_with_fallback(
            owner_id=-positive_group_id,
            op_name='messages.send',
            fn=call,
            user_token=user_token,
            community_tokens=community_tokens,
        )
        # messages.send returns an int (the new message_id) directly.
        new_mid = int(resp) if isinstance(resp, int) else None
        logger.info(
            "✅ Sent message to peer %s on behalf of group %s → mid=%s (via %s)",
            peer_id, positive_group_id, new_mid, via,
        )
        return {'success': True, 'message_id': new_mid, 'via': via}
    except ApiError as e:
        logger.warning(
            "Failed to send message to peer %s on group %s: [%s] %s",
            peer_id, positive_group_id, e.code, e,
        )
        return {'success': False, 'error_code': e.code, 'error': str(e)}
    except Exception as e:
        logger.error("Unexpected error sending message: %s", e)
        return {'success': False, 'error_code': 0, 'error': str(e)}
