"""
Service Activity Notifier - Уведомления о работе сервисов SETKA

Каждый сервис сам отправляет понятные сообщения о своей работе в мониторинг.
"""

import logging
import os
import sys
from enum import Enum
from typing import Any, Dict, Optional

# Добавляем путь к проекту
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.service_notifications import (
    NotificationType,
    ServiceNotification,
    service_notifications,
)
from utils.timezone import now_moscow

logger = logging.getLogger(__name__)


class ServiceActivityType(Enum):
    """Типы активности сервисов"""

    POST_COLLECTION_START = "post_collection_start"
    POST_COLLECTION_PROGRESS = "post_collection_progress"
    POST_COLLECTION_COMPLETE = "post_collection_complete"
    POST_SORTING_START = "post_sorting_start"
    POST_SORTING_PROGRESS = "post_sorting_progress"
    POST_SORTING_COMPLETE = "post_sorting_complete"
    DIGEST_CREATION_START = "digest_creation_start"
    DIGEST_CREATION_COMPLETE = "digest_creation_complete"
    DIGEST_PUBLISHING_START = "digest_publishing_start"
    DIGEST_PUBLISHING_COMPLETE = "digest_publishing_complete"
    VK_NOTIFICATIONS_CHECK_START = "vk_notifications_check_start"
    VK_NOTIFICATIONS_CHECK_COMPLETE = "vk_notifications_check_complete"
    HEALTH_CHECK_START = "health_check_start"
    HEALTH_CHECK_COMPLETE = "health_check_complete"
    SYSTEM_STARTUP = "system_startup"
    SYSTEM_SHUTDOWN = "system_shutdown"


class ServiceActivityNotifier:
    """Уведомления о работе сервисов"""

    def __init__(self):
        self.active_operations = {}  # Текущие операции по регионам/темам
        self.operation_history = []  # История операций
        self.max_history = 200

    def notify_post_collection_start(
        self, region_name: str, topic: str, communities_count: int = 0
    ):
        """Уведомление о начале сбора постов"""
        message = f"🔍 Начинаю сбор постов в {region_name} по теме '{topic}'"
        if communities_count > 0:
            message += f" ({communities_count} сообществ)"

        self._add_notification(
            ServiceActivityType.POST_COLLECTION_START,
            message,
            region=region_name,
            details={
                "topic": topic,
                "communities_count": communities_count,
                "operation_id": f"collect_{region_name}_{topic}_{now_moscow().strftime('%H%M')}",
            },
        )

        # Записываем активную операцию
        operation_id = f"collect_{region_name}_{topic}"
        self.active_operations[operation_id] = {
            "type": "post_collection",
            "region": region_name,
            "topic": topic,
            "started_at": now_moscow(),
            "communities_count": communities_count,
        }

    def notify_post_collection_progress(
        self,
        region_name: str,
        topic: str,
        processed_communities: int,
        total_communities: int,
        posts_found: int = 0,
    ):
        """Уведомление о прогрессе сбора постов"""
        progress_percent = (
            (processed_communities / total_communities * 100) if total_communities > 0 else 0
        )

        message = f"📊 Собираю посты в {region_name} по теме '{topic}': {processed_communities}/{total_communities} сообществ ({progress_percent:.0f}%)"  # noqa: E501
        if posts_found > 0:
            message += f", найдено {posts_found} постов"

        self._add_notification(
            ServiceActivityType.POST_COLLECTION_PROGRESS,
            message,
            region=region_name,
            details={
                "topic": topic,
                "processed_communities": processed_communities,
                "total_communities": total_communities,
                "progress_percent": progress_percent,
                "posts_found": posts_found,
            },
        )

    def notify_post_collection_complete(
        self, region_name: str, topic: str, total_posts: int, processing_time: float = 0
    ):
        """Уведомление о завершении сбора постов"""
        message = f"✅ Сбор постов в {region_name} по теме '{topic}' завершен"
        message += f" (найдено {total_posts} постов"
        if processing_time > 0:
            message += f", время: {processing_time:.1f}с"
        message += ")"

        self._add_notification(
            ServiceActivityType.POST_COLLECTION_COMPLETE,
            message,
            region=region_name,
            details={
                "topic": topic,
                "total_posts": total_posts,
                "processing_time": processing_time,
            },
        )

        # Удаляем активную операцию
        operation_id = f"collect_{region_name}_{topic}"
        if operation_id in self.active_operations:
            del self.active_operations[operation_id]

    def notify_post_sorting_start(self, region_name: str, topic: str, posts_count: int):
        """Уведомление о начале сортировки постов"""
        message = f"🔍 Сортирую {posts_count} постов по теме '{topic}' в {region_name}"

        self._add_notification(
            ServiceActivityType.POST_SORTING_START,
            message,
            region=region_name,
            details={"topic": topic, "posts_count": posts_count},
        )

        # Записываем активную операцию
        operation_id = f"sort_{region_name}_{topic}"
        self.active_operations[operation_id] = {
            "type": "post_sorting",
            "region": region_name,
            "topic": topic,
            "started_at": now_moscow(),
            "posts_count": posts_count,
        }

    def notify_post_sorting_progress(
        self,
        region_name: str,
        topic: str,
        processed_posts: int,
        total_posts: int,
        approved_posts: int = 0,
        rejected_posts: int = 0,
    ):
        """Уведомление о прогрессе сортировки постов"""
        progress_percent = (processed_posts / total_posts * 100) if total_posts > 0 else 0

        message = f"📊 Сортирую посты в {region_name}: {processed_posts}/{total_posts} ({progress_percent:.0f}%)"  # noqa: E501
        if approved_posts > 0 or rejected_posts > 0:
            message += f" (одобрено: {approved_posts}, отклонено: {rejected_posts})"

        self._add_notification(
            ServiceActivityType.POST_SORTING_PROGRESS,
            message,
            region=region_name,
            details={
                "topic": topic,
                "processed_posts": processed_posts,
                "total_posts": total_posts,
                "progress_percent": progress_percent,
                "approved_posts": approved_posts,
                "rejected_posts": rejected_posts,
            },
        )

    def notify_post_sorting_complete(
        self,
        region_name: str,
        topic: str,
        approved_posts: int,
        rejected_posts: int,
        processing_time: float = 0,
    ):
        """Уведомление о завершении сортировки постов"""
        total_posts = approved_posts + rejected_posts

        message = f"✅ Сортировка постов в {region_name} по теме '{topic}' завершена"
        message += f" (одобрено: {approved_posts}, отклонено: {rejected_posts}"
        if processing_time > 0:
            message += f", время: {processing_time:.1f}с"
        message += ")"

        self._add_notification(
            ServiceActivityType.POST_SORTING_COMPLETE,
            message,
            region=region_name,
            details={
                "topic": topic,
                "approved_posts": approved_posts,
                "rejected_posts": rejected_posts,
                "total_posts": total_posts,
                "processing_time": processing_time,
            },
        )

        # Удаляем активную операцию
        operation_id = f"sort_{region_name}_{topic}"
        if operation_id in self.active_operations:
            del self.active_operations[operation_id]

    def notify_digest_creation_start(self, region_name: str, topic: str, posts_count: int):
        """Уведомление о начале создания дайджеста"""
        message = f"📝 Создаю дайджест по теме '{topic}' для {region_name} ({posts_count} постов)"

        self._add_notification(
            ServiceActivityType.DIGEST_CREATION_START,
            message,
            region=region_name,
            details={"topic": topic, "posts_count": posts_count},
        )

        # Записываем активную операцию
        operation_id = f"digest_{region_name}_{topic}"
        self.active_operations[operation_id] = {
            "type": "digest_creation",
            "region": region_name,
            "topic": topic,
            "started_at": now_moscow(),
            "posts_count": posts_count,
        }

    def notify_digest_creation_complete(
        self, region_name: str, topic: str, digest_length: int, processing_time: float = 0
    ):
        """Уведомление о завершении создания дайджеста"""
        message = f"✅ Дайджест по теме '{topic}' для {region_name} создан"
        message += f" ({digest_length} символов"
        if processing_time > 0:
            message += f", время: {processing_time:.1f}с"
        message += ")"

        self._add_notification(
            ServiceActivityType.DIGEST_CREATION_COMPLETE,
            message,
            region=region_name,
            details={
                "topic": topic,
                "digest_length": digest_length,
                "processing_time": processing_time,
            },
        )

        # Удаляем активную операцию
        operation_id = f"digest_{region_name}_{topic}"
        if operation_id in self.active_operations:
            del self.active_operations[operation_id]

    def notify_digest_publishing_start(self, region_name: str, topic: str, channel: str = "VK"):
        """Уведомление о начале публикации дайджеста"""
        message = f"📤 Публикую дайджест от {topic} {region_name} в {channel}"

        self._add_notification(
            ServiceActivityType.DIGEST_PUBLISHING_START,
            message,
            region=region_name,
            details={"topic": topic, "channel": channel},
        )

        # Записываем активную операцию
        operation_id = f"publish_{region_name}_{topic}"
        self.active_operations[operation_id] = {
            "type": "digest_publishing",
            "region": region_name,
            "topic": topic,
            "started_at": now_moscow(),
            "channel": channel,
        }

    def notify_digest_publishing_complete(
        self,
        region_name: str,
        topic: str,
        channel: str = "VK",
        post_url: str = "",
        processing_time: float = 0,
    ):
        """Уведомление о завершении публикации дайджеста"""
        message = f"✅ Дайджест от {topic} {region_name} опубликован в {channel}"
        if post_url:
            message += " [ссылка]"
        if processing_time > 0:
            message += f" (время: {processing_time:.1f}с)"

        self._add_notification(
            ServiceActivityType.DIGEST_PUBLISHING_COMPLETE,
            message,
            region=region_name,
            details={
                "topic": topic,
                "channel": channel,
                "post_url": post_url,
                "processing_time": processing_time,
            },
        )

        # Удаляем активную операцию
        operation_id = f"publish_{region_name}_{topic}"
        if operation_id in self.active_operations:
            del self.active_operations[operation_id]

    def notify_vk_notifications_check_start(self, regions_count: int = 0):
        """Уведомление о начале проверки уведомлений VK"""
        message = "🔔 Проверяю предложенные посты и сообщения в главных группах"
        if regions_count > 0:
            message += f" ({regions_count} регионов)"

        self._add_notification(
            ServiceActivityType.VK_NOTIFICATIONS_CHECK_START,
            message,
            details={"regions_count": regions_count},
        )

    def notify_vk_notifications_check_complete(
        self, suggested_posts: int = 0, unread_messages: int = 0, processing_time: float = 0
    ):
        """Уведомление о завершении проверки уведомлений VK"""
        total_notifications = suggested_posts + unread_messages

        if total_notifications == 0:
            message = "✅ Опросил все главные сообщества на предмет предложек и сообщений. Уведомлений не найдено"  # noqa: E501
        else:
            message = f"✅ Опросил все главные сообщества на предмет предложек и сообщений. Найдено {total_notifications} уведомлений"  # noqa: E501
            if suggested_posts > 0:
                message += f" ({suggested_posts} предложений"
            if unread_messages > 0:
                message += (
                    f", {unread_messages} сообщений"
                    if suggested_posts > 0
                    else f" ({unread_messages} сообщений"
                )
            if suggested_posts > 0 or unread_messages > 0:
                message += ")"

        if processing_time > 0:
            message += f" (время: {processing_time:.1f}с)"

        self._add_notification(
            ServiceActivityType.VK_NOTIFICATIONS_CHECK_COMPLETE,
            message,
            details={
                "suggested_posts": suggested_posts,
                "unread_messages": unread_messages,
                "total_notifications": total_notifications,
                "processing_time": processing_time,
            },
        )

    def notify_health_check_start(self):
        """Уведомление о начале проверки здоровья системы"""
        message = "🏥 Проверяю состояние системы"

        self._add_notification(ServiceActivityType.HEALTH_CHECK_START, message, details={})

    def notify_health_check_complete(self, status: str, details: Dict[str, Any] = None):
        """Уведомление о завершении проверки здоровья системы"""
        if status == "healthy":
            message = "✅ Система работает нормально"
        elif status == "warning":
            message = "⚠️ Обнаружены предупреждения в работе системы"
        elif status == "error":
            message = "❌ Обнаружены ошибки в работе системы"
        else:
            message = f"ℹ️ Проверка системы завершена (статус: {status})"

        self._add_notification(
            ServiceActivityType.HEALTH_CHECK_COMPLETE, message, details=details or {}
        )

    def notify_system_startup(self):
        """Уведомление о запуске системы"""
        message = "🚀 Система SETKA запущена и готова к работе"

        self._add_notification(
            ServiceActivityType.SYSTEM_STARTUP,
            message,
            details={"startup_time": now_moscow().isoformat()},
        )

    def notify_system_shutdown(self):
        """Уведомление о завершении работы системы"""
        message = "🛑 Система SETKA завершает работу"

        self._add_notification(
            ServiceActivityType.SYSTEM_SHUTDOWN,
            message,
            details={"shutdown_time": now_moscow().isoformat()},
        )

    def _add_notification(
        self,
        activity_type: ServiceActivityType,
        message: str,
        region: Optional[str] = None,
        details: Optional[Dict] = None,
    ):
        """Добавить уведомление в систему"""
        # Определяем тип уведомления
        notification_type = NotificationType.SUCCESS
        if "error" in activity_type.value or "failure" in activity_type.value:
            notification_type = NotificationType.ERROR
        elif "warning" in activity_type.value:
            notification_type = NotificationType.ERROR
        elif "start" in activity_type.value or "startup" in activity_type.value:
            notification_type = NotificationType.SYSTEM_START

        # Создаем уведомление
        notification = ServiceNotification(
            notification_type, message, region=region, details=details or {}
        )

        # Добавляем в систему уведомлений
        service_notifications.add_notification(notification)

        # Сохраняем в историю
        self.operation_history.append(
            {
                "timestamp": now_moscow(),
                "type": activity_type.value,
                "message": message,
                "region": region,
                "details": details or {},
            }
        )

        # Ограничиваем историю
        if len(self.operation_history) > self.max_history:
            self.operation_history = self.operation_history[-self.max_history :]

        logger.info(f"Service activity notification: {message}")

    def get_active_operations(self) -> Dict[str, Dict]:
        """Получить текущие активные операции"""
        return self.active_operations.copy()

    def get_operation_history(self, limit: int = 50) -> list:
        """Получить историю операций"""
        recent = self.operation_history[-limit:] if self.operation_history else []
        return [
            {
                "timestamp": item["timestamp"].isoformat(),
                "type": item["type"],
                "message": item["message"],
                "region": item["region"],
                "details": item["details"],
            }
            for item in recent
        ]

    def get_system_status_summary(self) -> Dict[str, Any]:
        """Получить сводку статуса системы"""
        active_count = len(self.active_operations)
        recent_operations = len(self.operation_history)

        # Определяем общий статус
        if active_count > 0:
            status = "active"
            status_message = f"Выполняется {active_count} операций"
        else:
            status = "idle"
            status_message = "Система в режиме ожидания"

        return {
            "status": status,
            "status_message": status_message,
            "active_operations_count": active_count,
            "recent_operations_count": recent_operations,
            "active_operations": list(self.active_operations.keys()),
            "last_operation_time": (
                self.operation_history[-1]["timestamp"].isoformat()
                if self.operation_history
                else None
            ),
            "timestamp": now_moscow().isoformat(),
        }


# Глобальный экземпляр
service_activity_notifier = ServiceActivityNotifier()


# Удобные функции для быстрого использования
def notify_post_collection_start(region_name: str, topic: str, communities_count: int = 0):
    """Начать сбор постов"""
    service_activity_notifier.notify_post_collection_start(region_name, topic, communities_count)


def notify_post_collection_complete(
    region_name: str, topic: str, total_posts: int, processing_time: float = 0
):
    """Завершить сбор постов"""
    service_activity_notifier.notify_post_collection_complete(
        region_name, topic, total_posts, processing_time
    )


def notify_post_sorting_start(region_name: str, topic: str, posts_count: int):
    """Начать сортировку постов"""
    service_activity_notifier.notify_post_sorting_start(region_name, topic, posts_count)


def notify_post_sorting_complete(
    region_name: str,
    topic: str,
    approved_posts: int,
    rejected_posts: int,
    processing_time: float = 0,
):
    """Завершить сортировку постов"""
    service_activity_notifier.notify_post_sorting_complete(
        region_name, topic, approved_posts, rejected_posts, processing_time
    )


def notify_digest_creation_start(region_name: str, topic: str, posts_count: int):
    """Начать создание дайджеста"""
    service_activity_notifier.notify_digest_creation_start(region_name, topic, posts_count)


def notify_digest_creation_complete(
    region_name: str, topic: str, digest_length: int, processing_time: float = 0
):
    """Завершить создание дайджеста"""
    service_activity_notifier.notify_digest_creation_complete(
        region_name, topic, digest_length, processing_time
    )


def notify_digest_publishing_start(region_name: str, topic: str, channel: str = "VK"):
    """Начать публикацию дайджеста"""
    service_activity_notifier.notify_digest_publishing_start(region_name, topic, channel)


def notify_digest_publishing_complete(
    region_name: str,
    topic: str,
    channel: str = "VK",
    post_url: str = "",
    processing_time: float = 0,
):
    """Завершить публикацию дайджеста"""
    service_activity_notifier.notify_digest_publishing_complete(
        region_name, topic, channel, post_url, processing_time
    )


def notify_vk_notifications_check_start(regions_count: int = 0):
    """Начать проверку уведомлений VK"""
    service_activity_notifier.notify_vk_notifications_check_start(regions_count)


def notify_vk_notifications_check_complete(
    suggested_posts: int = 0, unread_messages: int = 0, processing_time: float = 0
):
    """Завершить проверку уведомлений VK"""
    service_activity_notifier.notify_vk_notifications_check_complete(
        suggested_posts, unread_messages, processing_time
    )


if __name__ == "__main__":
    # Тестирование
    print("🧪 Тестирование Service Activity Notifier")
    print("=" * 50)

    # Тестируем различные уведомления
    notify_post_collection_start("Кильмезский район", "Администрация", 5)
    notify_post_collection_complete("Кильмезский район", "Администрация", 12, 3.5)

    notify_post_sorting_start("Кильмезский район", "Администрация", 12)
    notify_post_sorting_complete("Кильмезский район", "Администрация", 8, 4, 2.1)

    notify_digest_creation_start("Кильмезский район", "Администрация", 8)
    notify_digest_creation_complete("Кильмезский район", "Администрация", 1200, 1.8)

    notify_digest_publishing_start("Кильмезский район", "Администрация", "VK")
    notify_digest_publishing_complete(
        "Кильмезский район", "Администрация", "VK", "https://vk.com/wall-123456_789", 0.9
    )

    notify_vk_notifications_check_start(15)
    notify_vk_notifications_check_complete(2, 0, 5.2)

    # Получаем результаты
    print("\n📋 Активные операции:")
    active = service_activity_notifier.get_active_operations()
    for op_id, op_data in active.items():
        print(f"  {op_id}: {op_data}")

    print("\n📊 Статус системы:")
    status = service_activity_notifier.get_system_status_summary()
    print(f"  Статус: {status['status']}")
    print(f"  Сообщение: {status['status_message']}")
    print(f"  Активных операций: {status['active_operations_count']}")

    print("\n📜 История операций:")
    history = service_activity_notifier.get_operation_history(5)
    for i, op in enumerate(history, 1):
        print(f"  {i}. {op['timestamp']}: {op['message']}")

    print("\n✅ Тест завершен!")
