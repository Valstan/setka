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
    """
    Запустить проверку ВСЕХ уведомлений прямо сейчас
    (suggested posts + unread messages + recent comments)
    
    Не зависит от расписания, можно запускать в любое время.
    """
    from database.connection import AsyncSessionLocal
    from database.models import Region
    from modules.notifications.unified_checker import UnifiedNotificationsChecker
    from modules.notifications.storage import NotificationsStorage
    from config.config_secure import VK_TOKENS, TELEGRAM_TOKENS, TELEGRAM_ALERT_CHAT_ID, SERVER
    from sqlalchemy import select
    from datetime import datetime
    
    try:
        async with AsyncSessionLocal() as session:
            # Получаем все регионы с главными группами (независимо от статуса активности)
            result = await session.execute(
                select(Region).where(
                    Region.vk_group_id.isnot(None)
                )
            )
            regions = list(result.scalars())
            
            if not regions:
                return {
                    'success': False,
                    'message': 'No regions with VK groups found',
                    'total_count': 0
                }
            
            # Подготавливаем данные
            region_groups = [
                {
                    'region_id': r.id,
                    'region_name': r.name,
                    'region_code': r.code,
                    'vk_group_id': r.vk_group_id
                }
                for r in regions
            ]
            
            # Проверяем через unified checker
            vk_token = VK_TOKENS.get("VALSTAN")
            if not vk_token:
                return {
                    'success': False,
                    'message': 'VK token not found',
                    'total_count': 0
                }
            
            checker = UnifiedNotificationsChecker(vk_token)
            result_data = await checker.check_all(region_groups)
            
            # Комментарии: запускаем в фоне через Celery (иначе запрос может превысить nginx timeout)
            comments_queued = False
            try:
                from tasks.celery_app import check_recent_comments as celery_check_recent_comments
                celery_check_recent_comments.delay()
                comments_queued = True
            except Exception as e:
                logger.warning(f"Failed to enqueue recent comments check: {e}")

            # В ответ отдаём текущее состояние комментариев из Redis (если уже есть),
            # а проверка обновит их через несколько секунд/минут.
            storage = NotificationsStorage()
            current_comments = storage.get_comments_notifications()
            result_data['recent_comments'] = current_comments
            result_data['comments_count'] = len(current_comments)
            result_data['total_count'] = result_data['total_count'] + result_data['comments_count']
            
            # Если есть уведомления, отправить в Telegram
            if result_data['total_count'] > 0:
                telegram_token = TELEGRAM_TOKENS.get("VALSTANBOT")
                chat_id = TELEGRAM_ALERT_CHAT_ID
                
                if telegram_token and chat_id:
                    dashboard_url = f"https://{SERVER['domain']}/notifications"
                    await checker.send_telegram_notification(
                        bot_token=telegram_token,
                        chat_id=chat_id,
                        notifications_data=result_data,
                        dashboard_url=dashboard_url
                    )
            
            return {
                'success': True,
                'timestamp': datetime.now().isoformat(),
                'total_count': result_data['total_count'],
                'suggested_count': result_data['suggested_count'],
                'messages_count': result_data['messages_count'],
                'comments_count': result_data.get('comments_count', 0),
                'comments_queued': comments_queued,
                'message': (
                    f"Found {result_data['suggested_count']} suggested posts, "
                    f"{result_data['messages_count']} unread messages, "
                    f"{result_data.get('comments_count', 0)} recent comments"
                )
            }
    
    except Exception as e:
        logger.error(f"Error in check_all_now: {e}", exc_info=True)
        return {
            'success': False,
            'message': str(e),
            'total_count': 0
        }

