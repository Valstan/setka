"""
Notifications API endpoints
"""
from fastapi import APIRouter
from typing import List, Dict, Any
from pydantic import BaseModel
import logging

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
        if suggested_data.get('timestamp'):
            suggested_timestamp = suggested_data['timestamp']
        
        # Получаем timestamp для messages (если есть отдельный ключ)
        messages_key = f"{storage.key_prefix}:unread_messages"
        messages_data_str = storage.redis_client.get(messages_key)
        if messages_data_str:
            import json
            messages_data = json.loads(messages_data_str)
            if messages_data.get('timestamp'):
                messages_timestamp = messages_data['timestamp']

        # Получаем timestamp для comments
        comments_key = f"{storage.key_prefix}:recent_comments"
        comments_data_str = storage.redis_client.get(comments_key)
        if comments_data_str:
            import json
            comments_data = json.loads(comments_data_str)
            if comments_data.get('timestamp'):
                comments_timestamp = comments_data['timestamp']
        
        # Используем более свежий timestamp (из 3 источников)
        candidates = [t for t in [suggested_timestamp, messages_timestamp, comments_timestamp] if t]
        if candidates:
            data['timestamp'] = max(candidates)
        
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
            'type': notification_type,
            'runs': storage.get_recent_runs(notification_type),
        }
    return {
        'suggested_posts': storage.get_recent_runs('suggested_posts'),
        'unread_messages': storage.get_recent_runs('unread_messages'),
        'recent_comments': storage.get_recent_runs('recent_comments'),
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
        'success': success,
        'message': 'Notifications cleared' if success else 'Failed to clear notifications'
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
    from database.connection import AsyncSessionLocal
    from database.models import Region, VKToken
    from modules.notifications.vk_suggested_checker import VKSuggestedChecker
    from modules.notifications.vk_messages_checker import VKMessagesChecker
    from modules.notifications.storage import NotificationsStorage
    from modules.notifications.telegram_alert import send_telegram_notifications_alert
    from modules.service_activity_notifier import (
        notify_vk_notifications_check_start,
        notify_vk_notifications_check_complete,
    )
    from config.runtime import VK_TOKENS, TELEGRAM_TOKENS, TELEGRAM_ALERT_CHAT_ID, SERVER
    from sqlalchemy import select
    from datetime import datetime

    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Region).where(Region.vk_group_id.isnot(None))
            )
            regions = list(result.scalars())

            if not regions:
                return {
                    'success': False,
                    'message': 'No regions with VK groups found',
                    'total_count': 0,
                }

            region_groups = [
                {
                    'region_id': r.id,
                    'region_name': r.name,
                    'region_code': r.code,
                    'vk_group_id': r.vk_group_id,
                }
                for r in regions
            ]

            vk_token = VK_TOKENS.get("VALSTAN")
            if not vk_token:
                return {
                    'success': False,
                    'message': 'VK token not found',
                    'total_count': 0,
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
            messages = messages_result['notifications']
            messages_denied = messages_result['denied_groups']

            processing_time = (datetime.now() - start_time).total_seconds()
            notify_vk_notifications_check_complete(len(suggested), len(messages), processing_time)

            # Сохраняем в Redis. Ручной запуск — без keep_if_empty: пользователь
            # явно сказал «обнови сейчас» и ожидает реального текущего состояния.
            storage = NotificationsStorage()
            storage.save_notifications(suggested, 'suggested_posts')
            storage.save_notifications(messages, 'unread_messages')
            storage.save_notifications(messages_denied, 'unread_messages_denied')

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
                    domain = SERVER.get('domain') or f"{SERVER.get('host', '127.0.0.1')}:{SERVER.get('port', 8000)}"
                    dashboard_url = f"https://{domain}/notifications"
                    await send_telegram_notifications_alert(
                        bot_token=telegram_token,
                        chat_id=chat_id,
                        notifications_data={
                            'suggested_count': len(suggested),
                            'messages_count': len(messages),
                            'total_count': total_count,
                            'suggested_posts': suggested,
                            'unread_messages': messages,
                        },
                        dashboard_url=dashboard_url,
                    )

            return {
                'success': True,
                'timestamp': datetime.now().isoformat(),
                'total_count': total_count,
                'suggested_count': len(suggested),
                'messages_count': len(messages),
                'messages_denied_count': len(messages_denied),
                'comments_count': len(current_comments),
                'comments_queued': comments_queued,
                'message': (
                    f"Found {len(suggested)} suggested posts, "
                    f"{len(messages)} unread messages, "
                    f"{len(current_comments)} recent comments"
                ),
            }

    except Exception as e:
        logger.error(f"Error in check_all_now: {e}", exc_info=True)
        return {
            'success': False,
            'message': str(e),
            'total_count': 0,
        }

