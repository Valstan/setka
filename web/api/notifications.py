"""
Notifications API endpoints
"""

import logging
from typing import List

from fastapi import APIRouter
from pydantic import BaseModel

from modules.notifications.storage import NotificationsStorage

logger = logging.getLogger(__name__)
router = APIRouter()


class NotificationResponse(BaseModel):
    """Notification response model"""

    region_id: int
    region_name: str
    region_code: str
    vk_group_id: int
    suggested_count: int
    url: str
    checked_at: str


class NotificationsResponse(BaseModel):
    """Notifications with metadata"""

    timestamp: str | None
    count: int
    notifications: List[NotificationResponse]


@router.get("/")
async def get_all_notifications():
    """
    Получить все текущие уведомления (suggested posts + unread messages)

    Returns:
        Dict с suggested posts и unread messages
    """
    storage = NotificationsStorage()
    data = storage.get_all_notifications()

    # Добавляем timestamp последней проверки из Redis
    suggested_timestamp = None
    messages_timestamp = None
    comments_timestamp = None

    try:
        # Получаем timestamp для suggested posts
        suggested_data = storage.get_notifications_with_timestamp()
        if suggested_data.get("timestamp"):
            suggested_timestamp = suggested_data["timestamp"]

        # Получаем timestamp для messages (если есть отдельный ключ)
        messages_key = f"{storage.key_prefix}:unread_messages"
        messages_data_str = storage.redis_client.get(messages_key)
        if messages_data_str:
            import json

            messages_data = json.loads(messages_data_str)
            if messages_data.get("timestamp"):
                messages_timestamp = messages_data["timestamp"]

        # Получаем timestamp для comments
        comments_key = f"{storage.key_prefix}:recent_comments"
        comments_data_str = storage.redis_client.get(comments_key)
        if comments_data_str:
            import json

            comments_data = json.loads(comments_data_str)
            if comments_data.get("timestamp"):
                comments_timestamp = comments_data["timestamp"]

        # Используем более свежий timestamp (из 3 источников)
        candidates = [t for t in [suggested_timestamp, messages_timestamp, comments_timestamp] if t]
        if candidates:
            data["timestamp"] = max(candidates)

    except Exception as e:
        logger.warning(f"Could not get timestamp from Redis: {e}")

    return data


@router.get("/suggested")
async def get_suggested_notifications():
    """
    Получить только уведомления о предложенных постах

    Returns:
        List уведомлений о suggested posts
    """
    storage = NotificationsStorage()
    return storage.get_notifications()


@router.get("/messages")
async def get_messages_notifications():
    """
    Получить только уведомления о непрочитанных сообщениях

    Returns:
        List уведомлений о unread messages
    """
    storage = NotificationsStorage()
    return storage.get_messages_notifications()


@router.get("/comments")
async def get_comments_notifications():
    """
    Получить только уведомления о комментариях за последние сутки

    Returns:
        List уведомлений о recent comments
    """
    storage = NotificationsStorage()
    return storage.get_comments_notifications()


class HandledRequest(BaseModel):
    """POST /handled body: mark notifications as handled."""

    notification_type: str  # 'recent_comment' | 'suggested_post' | 'unread_message'
    item_id: str  # comment_id / post_id / group_id (as string for flexibility)


@router.post("/handled")
async def mark_handled(req: HandledRequest):
    """Mark a specific notification as handled (etap 4a).

    Stored in Redis for 7 days. UI filters handled items out of the active
    list but can show them in archive view.
    """
    storage = NotificationsStorage()
    ok = storage.mark_handled(req.notification_type, req.item_id)
    return {"success": ok, "handled": ok}


@router.delete("/handled")
async def unmark_handled(req: HandledRequest):
    """Undo: remove the handled mark."""
    storage = NotificationsStorage()
    ok = storage.unmark_handled(req.notification_type, req.item_id)
    return {"success": ok}


@router.get("/handled/{notification_type}")
async def list_handled(notification_type: str):
    """All currently-handled item_ids for the given type."""
    storage = NotificationsStorage()
    return {
        "notification_type": notification_type,
        "ids": sorted(storage.get_handled_set(notification_type)),
    }


async def _load_vk_routing():
    """Return (user_token, community_tokens) for the VK action endpoints.

    Centralised so all `comments/like`, `comments/reply`, `messages/reply`
    endpoints follow the exact same routing without copy-paste drift.
    """
    from sqlalchemy import select

    from config.runtime import VK_TOKENS
    from database.connection import AsyncSessionLocal
    from database.models import VKToken

    vk_token = VK_TOKENS.get("VALSTAN")
    if not vk_token:
        return None, {}

    async with AsyncSessionLocal() as session:
        q = await session.execute(
            select(VKToken).where(
                VKToken.community_id.isnot(None),
                VKToken.is_active.is_(True),
            )
        )
        community_tokens = {t.community_id: t.token for t in q.scalars()}
    return vk_token, community_tokens


class LikeCommentRequest(BaseModel):
    """POST /comments/like body."""

    owner_id: int
    post_id: int
    comment_id: int


@router.post("/comments/like")
async def like_comment_endpoint(req: LikeCommentRequest):
    """Like a comment from the group account (etap 4a).

    Prefers the community-token of the owner group. Falls back to VALSTAN
    user-token if community-token fails with VK error 15/27 (no `manage` /
    no access). Idempotent — VK keeps a single like per (user, target).
    """
    from modules.notifications.vk_actions import like_comment

    vk_token, community_tokens = await _load_vk_routing()
    if not vk_token:
        return {"success": False, "error": "VK token not found"}

    return like_comment(
        owner_id=req.owner_id,
        post_id=req.post_id,
        comment_id=req.comment_id,
        user_token=vk_token,
        community_tokens=community_tokens,
    )


class ReplyCommentRequest(BaseModel):
    """POST /comments/reply body (etap 4b)."""

    owner_id: int
    post_id: int
    comment_id: int
    message: str


@router.post("/comments/reply")
async def reply_to_comment_endpoint(req: ReplyCommentRequest):
    """Reply to a comment from the group account (etap 4b).

    Calls VK `wall.createComment(reply_to_comment=..., from_group=1)` —
    new comment appears under the original thread, attributed to the
    community. Same two-token routing as like_comment.
    """
    from modules.notifications.vk_actions import reply_to_comment

    if not (req.message or "").strip():
        return {"success": False, "error": "message is empty"}

    vk_token, community_tokens = await _load_vk_routing()
    if not vk_token:
        return {"success": False, "error": "VK token not found"}

    return reply_to_comment(
        owner_id=req.owner_id,
        post_id=req.post_id,
        comment_id=req.comment_id,
        message=req.message,
        user_token=vk_token,
        community_tokens=community_tokens,
    )


class DraftReplyRequest(BaseModel):
    """POST /comments/draft body (etap 4b)."""

    text: str  # original comment text
    region_name: str | None = None
    style: str | None = None  # 'short' | 'friendly' | 'formal' (optional hint)


@router.post("/comments/draft")
async def draft_reply_endpoint(req: DraftReplyRequest):
    """Generate a draft reply via Groq (etap 4b).

    Returns {'draft': str, 'model': str} on success or
    {'success': False, 'error': str} on AI failure. The frontend pastes the
    draft into the reply textarea; the operator edits and sends manually.
    """
    from modules.notifications.ai_drafter import draft_comment_reply

    if not (req.text or "").strip():
        return {"success": False, "error": "text is empty"}

    return await draft_comment_reply(
        original_text=req.text,
        region_name=req.region_name,
        style=req.style,
    )


class SendMessageRequest(BaseModel):
    """POST /messages/reply body (etap 4b)."""

    group_id: int  # positive or negative; we abs() it
    peer_id: int  # VK user id or chat peer
    message: str


@router.post("/messages/reply")
async def send_message_endpoint(req: SendMessageRequest):
    """Send a direct message from the group to a conversation (etap 4b).

    Used by the templates UI to answer DMs to the community account. Routes
    through the community-token (preferred; user-token usually lacks the
    `messages` scope).
    """
    from modules.notifications.vk_actions import send_message

    if not (req.message or "").strip():
        return {"success": False, "error": "message is empty"}

    vk_token, community_tokens = await _load_vk_routing()
    if not vk_token:
        return {"success": False, "error": "VK token not found"}

    return send_message(
        group_id=req.group_id,
        peer_id=req.peer_id,
        message=req.message,
        user_token=vk_token,
        community_tokens=community_tokens,
    )


@router.get("/hot-posts")
async def get_hot_posts(min_comments: int = 5, limit: int = 5):
    """Top-N posts with the most discussion in the last 24h (etap 4a).

    Derived from the recent_comments Redis cache (already grouped by
    post_url). Not a separate VK query — uses what's already collected.
    """
    storage = NotificationsStorage()
    comments = storage.get_comments_notifications()
    if not comments:
        return {"posts": [], "min_comments": min_comments, "window_hours": 24}

    by_post: dict = {}
    handled_ids = storage.get_handled_set("recent_comment")
    for c in comments:
        url = c.get("post_url")
        if not url:
            continue
        bucket = by_post.setdefault(
            url,
            {
                "post_url": url,
                "region_name": c.get("region_name"),
                "region_code": c.get("region_code"),
                "vk_owner_id": c.get("vk_owner_id"),
                "vk_post_id": c.get("vk_post_id"),
                "total_comments": 0,
                "unhandled_comments": 0,
                "newest_at": None,
                "preview": None,
            },
        )
        bucket["total_comments"] += 1
        if str(c.get("comment_id")) not in handled_ids:
            bucket["unhandled_comments"] += 1
        ts = c.get("commented_at")
        if ts and (bucket["newest_at"] is None or ts > bucket["newest_at"]):
            bucket["newest_at"] = ts
        if not bucket["preview"]:
            bucket["preview"] = (c.get("text") or "")[:120]

    hot = [b for b in by_post.values() if b["total_comments"] >= min_comments]
    hot.sort(key=lambda b: (-b["unhandled_comments"], -b["total_comments"]))
    return {
        "posts": hot[:limit],
        "min_comments": min_comments,
        "window_hours": 24,
    }


@router.get("/history")
async def get_history(notification_type: str = None):
    """История последних запусков проверок (этап 3).

    Возвращает список последних ~48 проверок за 25ч для указанного типа
    (`suggested_posts` / `unread_messages` / `recent_comments`) или для всех
    сразу. Каждая запись содержит ts, count, duration_seconds, denied_count,
    success.

    Используется фронтом для виджета «активность за сутки».
    """
    storage = NotificationsStorage()
    if notification_type:
        return {
            "type": notification_type,
            "runs": storage.get_recent_runs(notification_type),
        }
    return {
        "suggested_posts": storage.get_recent_runs("suggested_posts"),
        "unread_messages": storage.get_recent_runs("unread_messages"),
        "recent_comments": storage.get_recent_runs("recent_comments"),
    }


@router.get("/stats")
async def get_stats():
    """Агрегаты по последним 24 часам проверок (этап 3).

    Для виджета «сколько прогонов, средняя длительность, последний результат».
    """
    storage = NotificationsStorage()
    return storage.get_stats()


@router.delete("/")
async def clear_notifications():
    """Очистить все уведомления"""
    storage = NotificationsStorage()
    success = storage.clear_notifications()

    return {
        "success": success,
        "message": "Notifications cleared" if success else "Failed to clear notifications",
    }


@router.post("/check-now")
async def check_all_now():
    """Запустить проверку ВСЕХ уведомлений прямо сейчас.

    Не зависит от расписания, можно запускать в любое время.

    Flow:
      1. SELECT регионы + community access tokens.
      2. VKSuggestedChecker.check_all_region_groups(...) синхронно.
      3. VKMessagesChecker.check_all_region_groups(...) синхронно.
      4. Сохраняем результаты в Redis (с keep_if_empty=False — ручной запуск
         даёт «свежее правдивее»).
      5. Comments — отдельная Celery таска `.delay()` (~25 сек, чтобы не
         упереться в nginx timeout); UI отрисует comments из Redis по мере
         появления.
      6. Если total > 0 — Telegram-алёрт.
    """
    from datetime import datetime

    from sqlalchemy import select

    from config.runtime import SERVER, TELEGRAM_ALERT_CHAT_ID, TELEGRAM_TOKENS, VK_TOKENS
    from database.connection import AsyncSessionLocal
    from database.models import Region, VKToken
    from modules.notifications.storage import NotificationsStorage
    from modules.notifications.telegram_alert import send_telegram_notifications_alert
    from modules.notifications.vk_messages_checker import VKMessagesChecker
    from modules.notifications.vk_suggested_checker import VKSuggestedChecker
    from modules.service_activity_notifier import (
        notify_vk_notifications_check_complete,
        notify_vk_notifications_check_start,
    )

    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(Region).where(Region.vk_group_id.isnot(None)))
            regions = list(result.scalars())

            if not regions:
                return {
                    "success": False,
                    "message": "No regions with VK groups found",
                    "total_count": 0,
                }

            region_groups = [
                {
                    "region_id": r.id,
                    "region_name": r.name,
                    "region_code": r.code,
                    "vk_group_id": r.vk_group_id,
                }
                for r in regions
            ]

            vk_token = VK_TOKENS.get("VALSTAN")
            if not vk_token:
                return {
                    "success": False,
                    "message": "VK token not found",
                    "total_count": 0,
                }

            community_q = await session.execute(
                select(VKToken).where(
                    VKToken.community_id.isnot(None),
                    VKToken.is_active.is_(True),
                )
            )
            community_tokens = {t.community_id: t.token for t in community_q.scalars()}

            # — Запуск двух проверок последовательно (одну после другой) —
            notify_vk_notifications_check_start(len(region_groups))
            start_time = datetime.now()

            suggested_checker = VKSuggestedChecker(vk_token, community_tokens=community_tokens)
            suggested = await suggested_checker.check_all_region_groups(region_groups)

            messages_checker = VKMessagesChecker(vk_token, community_tokens=community_tokens)
            messages_result = await messages_checker.check_all_region_groups(region_groups)
            messages = messages_result["notifications"]
            messages_denied = messages_result["denied_groups"]

            processing_time = (datetime.now() - start_time).total_seconds()
            notify_vk_notifications_check_complete(len(suggested), len(messages), processing_time)

            # Сохраняем в Redis. Ручной запуск — без keep_if_empty: пользователь
            # явно сказал «обнови сейчас» и ожидает реального текущего состояния.
            storage = NotificationsStorage()
            storage.save_notifications(suggested, "suggested_posts")
            storage.save_notifications(messages, "unread_messages")
            storage.save_notifications(messages_denied, "unread_messages_denied")

            # Comments в фоне через Celery (избегаем nginx timeout)
            comments_queued = False
            try:
                from tasks.celery_app import check_recent_comments as celery_check_recent_comments

                celery_check_recent_comments.delay()
                comments_queued = True
            except Exception as e:
                logger.warning(f"Failed to enqueue recent comments check: {e}")

            current_comments = storage.get_comments_notifications()
            total_count = len(suggested) + len(messages) + len(current_comments)

            # Telegram алёрт, если есть что показать
            if total_count > 0:
                telegram_token = TELEGRAM_TOKENS.get("VALSTANBOT")
                chat_id = TELEGRAM_ALERT_CHAT_ID
                if telegram_token and chat_id:
                    domain = (
                        SERVER.get("domain")
                        or f"{SERVER.get('host', '127.0.0.1')}:{SERVER.get('port', 8000)}"
                    )
                    dashboard_url = f"https://{domain}/notifications"
                    await send_telegram_notifications_alert(
                        bot_token=telegram_token,
                        chat_id=chat_id,
                        notifications_data={
                            "suggested_count": len(suggested),
                            "messages_count": len(messages),
                            "comments_count": len(current_comments),
                            "total_count": total_count,
                            "suggested_posts": suggested,
                            "unread_messages": messages,
                        },
                        dashboard_url=dashboard_url,
                    )

            return {
                "success": True,
                "timestamp": datetime.now().isoformat(),
                "total_count": total_count,
                "suggested_count": len(suggested),
                "messages_count": len(messages),
                "messages_denied_count": len(messages_denied),
                "comments_count": len(current_comments),
                "comments_queued": comments_queued,
                "message": (
                    f"Found {len(suggested)} suggested posts, "
                    f"{len(messages)} unread messages, "
                    f"{len(current_comments)} recent comments"
                ),
            }

    except Exception as e:
        logger.error(f"Error in check_all_now: {e}", exc_info=True)
        return {
            "success": False,
            "message": str(e),
            "total_count": 0,
        }
