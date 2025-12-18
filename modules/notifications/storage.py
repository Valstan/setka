"""
Notifications Storage

Хранение уведомлений в Redis.
Уведомления хранятся 24 часа и обновляются каждый час.
"""
import json
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime
import redis

logger = logging.getLogger(__name__)


class NotificationsStorage:
    """Хранилище уведомлений в Redis"""
    
    def __init__(self, redis_host: str = "localhost", redis_port: int = 6379, redis_db: int = 1):
        """
        Инициализация хранилища
        
        Args:
            redis_host: Хост Redis
            redis_port: Порт Redis
            redis_db: Номер БД Redis (используем 1, чтобы не мешать Celery в 0)
        """
        self.redis_client = redis.Redis(
            host=redis_host,
            port=redis_port,
            db=redis_db,
            decode_responses=True
        )
        self.key_prefix = "setka:notifications"
        
    def save_notifications(self, notifications: List[Dict[str, Any]], notification_type: str = 'suggested_posts') -> bool:
        """
        Сохранить список уведомлений (заменяет все старые)
        
        Args:
            notifications: Список уведомлений
            notification_type: Тип уведомлений ('suggested_posts', 'unread_messages', 'recent_comments', ...)
            
        Returns:
            True если успешно
        """
        try:
            key = f"{self.key_prefix}:{notification_type}"
            
            # Сохраняем как JSON с timestamp
            data = {
                'timestamp': datetime.now().isoformat(),
                'notifications': notifications
            }
            
            # Сохраняем с TTL 24 часа
            self.redis_client.setex(
                key,
                86400,  # 24 часа
                json.dumps(data, ensure_ascii=False)
            )
            
            logger.info(f"Saved {len(notifications)} {notification_type} notifications to Redis")
            return True
            
        except Exception as e:
            logger.error(f"Failed to save {notification_type} notifications: {e}")
            return False
    
    def get_notifications(self) -> List[Dict[str, Any]]:
        """
        Получить текущие уведомления
        
        Returns:
            Список уведомлений
        """
        try:
            key = f"{self.key_prefix}:suggested_posts"
            data_str = self.redis_client.get(key)
            
            if not data_str:
                return []
            
            data = json.loads(data_str)
            return data.get('notifications', [])
            
        except Exception as e:
            logger.error(f"Failed to get notifications: {e}")
            return []
    
    def get_notifications_with_timestamp(self) -> Dict[str, Any]:
        """
        Получить уведомления с timestamp последнего обновления
        
        Returns:
            Dict с notifications и timestamp
        """
        try:
            key = f"{self.key_prefix}:suggested_posts"
            data_str = self.redis_client.get(key)
            
            if not data_str:
                return {
                    'timestamp': None,
                    'notifications': []
                }
            
            return json.loads(data_str)
            
        except Exception as e:
            logger.error(f"Failed to get notifications: {e}")
            return {
                'timestamp': None,
                'notifications': []
            }
    
    def get_messages_notifications(self) -> List[Dict[str, Any]]:
        """
        Получить уведомления о непрочитанных сообщениях
        
        Returns:
            Список уведомлений о сообщениях
        """
        try:
            key = f"{self.key_prefix}:unread_messages"
            data_str = self.redis_client.get(key)
            
            if not data_str:
                return []
            
            data = json.loads(data_str)
            return data.get('notifications', [])
            
        except Exception as e:
            logger.error(f"Failed to get messages notifications: {e}")
            return []
    
    def get_comments_notifications(self) -> List[Dict[str, Any]]:
        """
        Получить уведомления о свежих комментариях (за сутки)

        Returns:
            Список уведомлений о комментариях
        """
        try:
            key = f"{self.key_prefix}:recent_comments"
            data_str = self.redis_client.get(key)

            if not data_str:
                return []

            data = json.loads(data_str)
            return data.get('notifications', [])

        except Exception as e:
            logger.error(f"Failed to get comments notifications: {e}")
            return []

    def get_all_notifications(self) -> Dict[str, Any]:
        """
        Получить все уведомления (suggested posts + unread messages + recent comments)
        
        Returns:
            Dict с объединёнными уведомлениями:
                - suggested_posts: List
                - unread_messages: List
                - total_count: int
                - suggested_count: int
                - messages_count: int
                - comments_count: int
                - timestamp: str (НЕ перезаписывается, остается None для внешней логики)
        """
        try:
            suggested = self.get_notifications()
            messages = self.get_messages_notifications()
            comments = self.get_comments_notifications()
            
            return {
                'suggested_posts': suggested,
                'unread_messages': messages,
                'recent_comments': comments,
                'total_count': len(suggested) + len(messages) + len(comments),
                'suggested_count': len(suggested),
                'messages_count': len(messages),
                'comments_count': len(comments),
                'timestamp': None  # Будет установлен в API endpoint
            }
            
        except Exception as e:
            logger.error(f"Failed to get all notifications: {e}")
            return {
                'suggested_posts': [],
                'unread_messages': [],
                'recent_comments': [],
                'total_count': 0,
                'suggested_count': 0,
                'messages_count': 0,
                'comments_count': 0,
                'timestamp': None
            }
    
    def clear_notifications(self, notification_type: str = None) -> bool:
        """
        Очистить уведомления
        
        Args:
            notification_type: Тип для очистки ('suggested_posts', 'unread_messages', 'recent_comments' или None для всех)
        
        Returns:
            True если успешно
        """
        try:
            if notification_type:
                # Очистить конкретный тип
                key = f"{self.key_prefix}:{notification_type}"
                self.redis_client.delete(key)
                logger.info(f"Cleared {notification_type} notifications")
            else:
                # Очистить все
                pattern = f"{self.key_prefix}:*"
                keys = self.redis_client.keys(pattern)
                if keys:
                    self.redis_client.delete(*keys)
                logger.info(f"Cleared all notifications ({len(keys) if keys else 0} keys)")
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to clear notifications: {e}")
            return False

