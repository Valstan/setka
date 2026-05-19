"""
Unified Notifications Checker

Объединённый мониторинг:
1. Предложенных постов (suggested posts)
2. Непрочитанных сообщений (unread messages)

Проверяет одновременно, раз в час, уведомляет в Telegram
"""
import logging
import asyncio
from typing import List, Dict, Any
from datetime import datetime

from modules.notifications.vk_suggested_checker import VKSuggestedChecker
from modules.notifications.vk_messages_checker import VKMessagesChecker
from modules.notifications.storage import NotificationsStorage
from modules.service_activity_notifier import (
    notify_vk_notifications_check_start, notify_vk_notifications_check_complete
)

logger = logging.getLogger(__name__)


class UnifiedNotificationsChecker:
    """
    Объединённый checker для всех типов VK уведомлений
    
    Проверяет:
    - Предложенные посты (suggested)
    - Непрочитанные сообщения (messages)
    """
    
    def __init__(self, vk_token: str):
        """
        Инициализация unified checker
        
        Args:
            vk_token: VK access token с правами на группы и сообщения
        """
        self.suggested_checker = VKSuggestedChecker(vk_token)
        self.messages_checker = VKMessagesChecker(vk_token)
        self.storage = NotificationsStorage()
        
        logger.info("Unified Notifications Checker initialized")
    
    async def check_all(self, region_groups: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Проверить все уведомления для всех регионов
        
        Args:
            region_groups: Список главных групп регионов с полями:
                - region_id: int
                - region_name: str
                - region_code: str
                - vk_group_id: int
        
        Returns:
            Dict с результатами:
                - suggested_posts: List - предложенные посты
                - unread_messages: List - непрочитанные сообщения
                - total_count: int - всего уведомлений
                - checked_at: str - время проверки
        """
        logger.info(f"Checking notifications for {len(region_groups)} region groups...")
        
        # Уведомляем о начале проверки
        notify_vk_notifications_check_start(len(region_groups))
        
        start_time = datetime.now()
        
        # Проверяем suggested posts и messages параллельно
        suggested_notifications = await self.suggested_checker.check_all_region_groups(region_groups)
        messages_result = await self.messages_checker.check_all_region_groups(region_groups)
        messages_notifications = messages_result['notifications']
        messages_denied = messages_result['denied_groups']

        # Сохраняем в Redis
        self.storage.save_notifications(suggested_notifications, 'suggested_posts')
        self.storage.save_notifications(messages_notifications, 'unread_messages')
        self.storage.save_notifications(messages_denied, 'unread_messages_denied')

        # Подготавливаем результат
        result = {
            'suggested_posts': suggested_notifications,
            'unread_messages': messages_notifications,
            'unread_messages_denied': messages_denied,
            'suggested_count': len(suggested_notifications),
            'messages_count': len(messages_notifications),
            'messages_denied_count': len(messages_denied),
            'total_count': len(suggested_notifications) + len(messages_notifications),
            'checked_at': datetime.now().isoformat()
        }
        
        # Уведомляем о завершении проверки
        processing_time = (datetime.now() - start_time).total_seconds()
        notify_vk_notifications_check_complete(
            result['suggested_count'], 
            result['messages_count'], 
            processing_time
        )
        
        logger.info(
            f"✅ Check complete: "
            f"{result['suggested_count']} suggested, "
            f"{result['messages_count']} unread messages, "
            f"total: {result['total_count']}"
        )
        
        return result
    
    def get_all_notifications(self) -> Dict[str, Any]:
        """
        Получить все сохранённые уведомления
        
        Returns:
            Dict с suggested и messages уведомлениями
        """
        return self.storage.get_all_notifications()
    
    def has_any_notifications(self) -> bool:
        """
        Проверить, есть ли какие-либо уведомления
        
        Returns:
            True если есть хотя бы одно уведомление
        """
        all_notifs = self.storage.get_all_notifications()
        return all_notifs['total_count'] > 0
    
    async def send_telegram_notification(
        self,
        bot_token: str,
        chat_id: str,
        notifications_data: Dict[str, Any],
        dashboard_url: str
    ) -> bool:
        """
        Отправить Telegram уведомление о новых предложках и сообщениях
        
        Args:
            bot_token: Telegram bot token
            chat_id: Chat ID для уведомлений
            notifications_data: Данные уведомлений (из check_all)
            dashboard_url: URL кабинета уведомлений в SETKA
            
        Returns:
            True если успешно отправлено
        """
        try:
            from telegram import Bot
            import asyncio
            
            bot = Bot(token=bot_token)
            
            suggested_count = notifications_data.get('suggested_count', 0)
            messages_count = notifications_data.get('messages_count', 0)
            total = notifications_data.get('total_count', 0)
            
            # Формируем сообщение
            message_parts = ["📬 <b>Новые уведомления SETKA</b>\n"]
            
            if suggested_count > 0:
                message_parts.append(f"📝 Предложенных постов: <b>{suggested_count}</b>")
                
                # Детали по регионам
                suggested = notifications_data.get('suggested_posts', [])
                for notif in suggested[:5]:  # Показываем первые 5
                    region_name = notif.get('region_name', '?')
                    count = notif.get('suggested_count', 0)
                    message_parts.append(f"  • {region_name}: {count} пост(ов)")
                
                if len(suggested) > 5:
                    message_parts.append(f"  ... и ещё {len(suggested) - 5} регион(ов)")
            
            if messages_count > 0:
                message_parts.append(f"\n💬 Непрочитанных сообщений: <b>{messages_count}</b>")
                
                # Детали по регионам
                messages = notifications_data.get('unread_messages', [])
                for notif in messages[:5]:  # Показываем первые 5
                    region_name = notif.get('region_name', '?')
                    count = notif.get('unread_count', 0)
                    message_parts.append(f"  • {region_name}: {count} сообщ.")
                
                if len(messages) > 5:
                    message_parts.append(f"  ... и ещё {len(messages) - 5} регион(ов)")
            
            # Добавляем ссылку на кабинет
            message_parts.append(f"\n🔗 <a href='{dashboard_url}'>Открыть кабинет уведомлений</a>")
            message_parts.append(f"\n🕐 Проверено: {datetime.now().strftime('%H:%M')}")
            
            message = "\n".join(message_parts)
            
            # Отправляем
            await bot.send_message(
                chat_id=chat_id,
                text=message,
                parse_mode='HTML',
                disable_web_page_preview=True
            )
            
            logger.info(f"✅ Telegram notification sent (total: {total})")
            return True
            
        except Exception as e:
            logger.error(f"Failed to send Telegram notification: {e}")
            return False
    
    def clear_notifications(self, notification_type: str = None) -> bool:
        """
        Очистить уведомления
        
        Args:
            notification_type: Тип для очистки ('suggested_posts', 'unread_messages', или None для всех)
        
        Returns:
            True если успешно
        """
        return self.storage.clear_notifications(notification_type)
