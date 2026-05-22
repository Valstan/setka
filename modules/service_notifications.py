"""
Service Notifications System - Мониторинг работы системы SETKA
"""

import logging
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class NotificationType(Enum):
    """Типы сервисных уведомлений"""

    SYSTEM_START = "system_start"
    REGION_START = "region_start"
    TOPIC_SELECT = "topic_select"
    COMMUNITY_SCAN = "community_scan"
    POST_FILTER = "post_filter"
    POST_SELECT = "post_select"
    PUBLISH_VK = "publish_vk"
    PUBLISH_TELEGRAM = "publish_telegram"
    PUBLISH_OK = "publish_ok"
    SUCCESS = "success"
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"
    PUBLISH_WEBSITE = "publish_website"
    SYSTEM_PAUSE = "system_pause"
    REGION_QUEUE = "region_queue"


class ServiceNotification:
    """Сервисное уведомление"""

    def __init__(
        self,
        notification_type: NotificationType,
        message: str,
        region: Optional[str] = None,
        topic: Optional[str] = None,
        community: Optional[str] = None,
        post_id: Optional[str] = None,
        details: Optional[Dict] = None,
    ):
        self.timestamp = datetime.now()
        self.type = notification_type
        self.message = message
        self.region = region
        self.topic = topic
        self.community = community
        self.post_id = post_id
        self.details = details or {}

    def to_dict(self) -> Dict:
        """Преобразовать в словарь"""
        return {
            "timestamp": self.timestamp.isoformat(),
            "type": self.type.value,
            "message": self.message,
            "region": self.region,
            "topic": self.topic,
            "community": self.community,
            "post_id": self.post_id,
            "details": self.details,
        }

    def to_string(self) -> str:
        """Преобразовать в строку для логов"""
        timestamp_str = self.timestamp.strftime("%H:%M:%S")

        parts = [f"[{timestamp_str}]"]

        if self.region:
            parts.append(f"[{self.region}]")

        if self.topic:
            parts.append(f"[{self.topic}]")

        parts.append(self.message)

        if self.community:
            parts.append(f"(сообщество: {self.community})")

        if self.post_id:
            parts.append(f"(пост: {self.post_id})")

        return " ".join(parts)


class ServiceNotificationManager:
    """Менеджер сервисных уведомлений"""

    def __init__(self, max_notifications: int = 1000):
        self.notifications: List[ServiceNotification] = []
        self.max_notifications = max_notifications
        self.current_region: Optional[str] = None
        self.current_topic: Optional[str] = None
        self.is_running: bool = False

    def add_notification(self, notification: ServiceNotification):
        """Добавить уведомление"""
        self.notifications.append(notification)

        # Ограничиваем количество уведомлений
        if len(self.notifications) > self.max_notifications:
            self.notifications = self.notifications[-self.max_notifications :]

        # Логируем
        logger.info(f"SERVICE: {notification.to_string()}")

    def system_start(self, region: str):
        """Система запущена для региона"""
        self.is_running = True
        self.current_region = region
        notification = ServiceNotification(
            NotificationType.SYSTEM_START, f"🚀 Запускаем в работу регион {region}", region=region
        )
        self.add_notification(notification)

    def topic_select(self, topic: str):
        """Выбрана тема"""
        self.current_topic = topic
        notification = ServiceNotification(
            NotificationType.TOPIC_SELECT,
            f'📝 Выбираем тему "{topic}"',
            region=self.current_region,
            topic=topic,
        )
        self.add_notification(notification)

    def community_scan(self, community: str, count: int):
        """Сканирование сообщества"""
        notification = ServiceNotification(
            NotificationType.COMMUNITY_SCAN,
            f'🔍 Собираем информацию из сообществ из раздела "{self.current_topic}"',
            region=self.current_region,
            topic=self.current_topic,
            community=community,
            details={"count": count},
        )
        self.add_notification(notification)

    def post_filter(self, filtered_count: int, total_count: int):
        """Фильтрация постов"""
        notification = ServiceNotification(
            NotificationType.POST_FILTER,
            f"🔧 Фильтрую-сортирую посты ({filtered_count} из {total_count})",
            region=self.current_region,
            topic=self.current_topic,
            details={"filtered": filtered_count, "total": total_count},
        )
        self.add_notification(notification)

    def post_select(self, post_id: str, community: str):
        """Выбран пост для публикации"""
        notification = ServiceNotification(
            NotificationType.POST_SELECT,
            f"✅ Выбрал один пост самый подходящий для публикации в {self.current_region}-Инфо",
            region=self.current_region,
            topic=self.current_topic,
            community=community,
            post_id=post_id,
        )
        self.add_notification(notification)

    def publish_vk(self, post_id: str, success: bool = True):
        """Публикация в VK"""
        status = "✅" if success else "❌"
        notification = ServiceNotification(
            NotificationType.PUBLISH_VK,
            f"{status} Публикую пост в ВК сообщество {self.current_region}-Инфо",
            region=self.current_region,
            post_id=post_id,
            details={"success": success},
        )
        self.add_notification(notification)

    def publish_telegram(self, post_id: str, success: bool = True):
        """Публикация в Telegram"""
        status = "✅" if success else "❌"
        notification = ServiceNotification(
            NotificationType.PUBLISH_TELEGRAM,
            f"{status} Публикую пост в телеграм-канал {self.current_region}-Инфо",
            region=self.current_region,
            post_id=post_id,
            details={"success": success},
        )
        self.add_notification(notification)

    def publish_ok(self, post_id: str, success: bool = True):
        """Публикация в Одноклассники"""
        status = "✅" if success else "❌"
        notification = ServiceNotification(
            NotificationType.PUBLISH_OK,
            f"{status} Публикую пост Одноклассники сообщество {self.current_region} Инфо",
            region=self.current_region,
            post_id=post_id,
            details={"success": success},
        )
        self.add_notification(notification)

    def publish_website(self, post_id: str, success: bool = True):
        """Публикация на сайт"""
        status = "✅" if success else "❌"
        notification = ServiceNotification(
            NotificationType.PUBLISH_WEBSITE,
            f"{status} Публикую пост на сайт {self.current_region}-Инфо",
            region=self.current_region,
            post_id=post_id,
            details={"success": success},
        )
        self.add_notification(notification)

    def system_pause(self):
        """Система на паузе"""
        notification = ServiceNotification(
            NotificationType.SYSTEM_PAUSE, "⏸️ Встаю на паузу", region=self.current_region
        )
        self.add_notification(notification)

    def region_queue(self, next_region: str):
        """Следующий регион в очереди"""
        notification = ServiceNotification(
            NotificationType.REGION_QUEUE,
            f"📋 Запускаю в очередь на работу регион {next_region}",
            region=next_region,
        )
        self.add_notification(notification)

    def error(self, message: str, details: Optional[Dict] = None):
        """Ошибка"""
        notification = ServiceNotification(
            NotificationType.ERROR,
            f"❌ ОШИБКА: {message}",
            region=self.current_region,
            topic=self.current_topic,
            details=details,
        )
        self.add_notification(notification)

    def success(self, message: str, details: Optional[Dict] = None):
        """Успех"""
        notification = ServiceNotification(
            NotificationType.SUCCESS,
            f"✅ УСПЕХ: {message}",
            region=self.current_region,
            topic=self.current_topic,
            details=details,
        )
        self.add_notification(notification)

    def get_recent_notifications(self, limit: int = 50) -> List[Dict]:
        """Получить последние уведомления"""
        recent = self.notifications[-limit:] if self.notifications else []
        return [n.to_dict() for n in recent]

    def get_notifications_by_type(self, notification_type: NotificationType) -> List[Dict]:
        """Получить уведомления по типу"""
        filtered = [n for n in self.notifications if n.type == notification_type]
        return [n.to_dict() for n in filtered]

    def get_status(self) -> Dict:
        """Получить текущий статус системы"""
        return {
            "is_running": self.is_running,
            "current_region": self.current_region,
            "current_topic": self.current_topic,
            "total_notifications": len(self.notifications),
            "last_notification": self.notifications[-1].to_dict() if self.notifications else None,
        }

    def clear_notifications(self):
        """Очистить все уведомления"""
        self.notifications.clear()
        logger.info("SERVICE: Уведомления очищены")


# Глобальный экземпляр менеджера
service_notifications = ServiceNotificationManager()
