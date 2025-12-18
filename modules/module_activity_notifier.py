"""
Module Activity Notifier - Уведомления о работе модулей системы

Модули системы используют этот класс для отправки уведомлений о своей работе
в окно монитора в реальном времени.
"""
import logging
from datetime import datetime
from typing import Dict, Any, Optional, List
from enum import Enum

from modules.service_notifications import service_notifications, NotificationType, ServiceNotification
from utils.timezone import now_moscow

logger = logging.getLogger(__name__)


class ModuleActivityType(Enum):
    """Типы активности модулей"""
    # VK Monitor
    VK_SCAN_STARTED = "vk_scan_started"
    VK_SCAN_COMPLETED = "vk_scan_completed"
    VK_SCAN_ERROR = "vk_scan_error"
    VK_POSTS_FOUND = "vk_posts_found"
    
    # AI Analyzer
    AI_ANALYSIS_STARTED = "ai_analysis_started"
    AI_ANALYSIS_COMPLETED = "ai_analysis_completed"
    AI_ANALYSIS_ERROR = "ai_analysis_error"
    
    # Filter Pipeline
    FILTER_STARTED = "filter_started"
    FILTER_COMPLETED = "filter_completed"
    FILTER_REJECTED = "filter_rejected"
    
    # Publisher
    PUBLISH_STARTED = "publish_started"
    PUBLISH_COMPLETED = "publish_completed"
    PUBLISH_ERROR = "publish_error"
    
    # Workflow
    WORKFLOW_STARTED = "workflow_started"
    WORKFLOW_COMPLETED = "workflow_completed"
    WORKFLOW_ERROR = "workflow_error"
    REGION_PROCESSING = "region_processing"
    
    # Database
    DB_OPERATION = "db_operation"
    DB_ERROR = "db_error"
    
    # System
    SYSTEM_STARTUP = "system_startup"
    SYSTEM_SHUTDOWN = "system_shutdown"
    SYSTEM_HEALTH = "system_health"


class ModuleActivityNotifier:
    """Уведомления о работе модулей"""
    
    def __init__(self):
        self.module_stats = {}
        self.last_activities = {}
    
    def notify_activity(
        self,
        module_name: str,
        activity_type: ModuleActivityType,
        message: str,
        details: Optional[Dict[str, Any]] = None,
        region: Optional[str] = None
    ):
        """
        Отправить уведомление о работе модуля
        
        Args:
            module_name: Название модуля (vk_monitor, ai_analyzer, etc.)
            activity_type: Тип активности
            message: Сообщение о работе
            details: Дополнительные детали
            region: Регион (если применимо)
        """
        try:
            # Определяем тип уведомления
            notification_type = self._get_notification_type(activity_type)
            
            # Формируем заголовок
            title = self._format_title(module_name, activity_type, region)
            
            # Формируем детали
            formatted_details = self._format_details(details, activity_type)
            
            # Создаем уведомление
            notification = ServiceNotification(
                notification_type=notification_type,
                message=f"{title}: {message}",
                region=region,
                details=formatted_details
            )
            
            # Отправляем уведомление
            service_notifications.add_notification(notification)
            
            # Обновляем статистику модуля
            self._update_module_stats(module_name, activity_type)
            
            # Логируем
            logger.info(f"[{module_name}] {activity_type.value}: {message}")
            
        except Exception as e:
            logger.error(f"Failed to send module activity notification: {e}")
    
    def _get_notification_type(self, activity_type: ModuleActivityType) -> NotificationType:
        """Определить тип уведомления по типу активности"""
        if "error" in activity_type.value:
            return NotificationType.ERROR
        elif "completed" in activity_type.value or "found" in activity_type.value:
            return NotificationType.SUCCESS
        elif "started" in activity_type.value:
            return NotificationType.SYSTEM_START
        elif "rejected" in activity_type.value:
            return NotificationType.WARNING
        else:
            return NotificationType.INFO
    
    def _format_title(self, module_name: str, activity_type: ModuleActivityType, region: Optional[str] = None) -> str:
        """Форматировать заголовок уведомления"""
        module_display = self._get_module_display_name(module_name)
        activity_display = self._get_activity_display_name(activity_type)
        
        if region:
            return f"{module_display} - {activity_display} ({region.upper()})"
        else:
            return f"{module_display} - {activity_display}"
    
    def _get_module_display_name(self, module_name: str) -> str:
        """Получить отображаемое название модуля"""
        display_names = {
            "vk_monitor": "🔍 VK Monitor",
            "ai_analyzer": "🤖 AI Analyzer", 
            "filter_pipeline": "🔧 Filter Pipeline",
            "publisher": "📤 Publisher",
            "workflow": "⚙️ Workflow",
            "database": "💾 Database",
            "system": "🖥️ System"
        }
        return display_names.get(module_name, f"📦 {module_name}")
    
    def _get_activity_display_name(self, activity_type: ModuleActivityType) -> str:
        """Получить отображаемое название активности"""
        display_names = {
            ModuleActivityType.VK_SCAN_STARTED: "Сканирование начато",
            ModuleActivityType.VK_SCAN_COMPLETED: "Сканирование завершено",
            ModuleActivityType.VK_SCAN_ERROR: "Ошибка сканирования",
            ModuleActivityType.VK_POSTS_FOUND: "Посты найдены",
            
            ModuleActivityType.AI_ANALYSIS_STARTED: "AI анализ начат",
            ModuleActivityType.AI_ANALYSIS_COMPLETED: "AI анализ завершен",
            ModuleActivityType.AI_ANALYSIS_ERROR: "Ошибка AI анализа",
            
            ModuleActivityType.FILTER_STARTED: "Фильтрация начата",
            ModuleActivityType.FILTER_COMPLETED: "Фильтрация завершена",
            ModuleActivityType.FILTER_REJECTED: "Посты отклонены",
            
            ModuleActivityType.PUBLISH_STARTED: "Публикация начата",
            ModuleActivityType.PUBLISH_COMPLETED: "Публикация завершена",
            ModuleActivityType.PUBLISH_ERROR: "Ошибка публикации",
            
            ModuleActivityType.WORKFLOW_STARTED: "Workflow запущен",
            ModuleActivityType.WORKFLOW_COMPLETED: "Workflow завершен",
            ModuleActivityType.WORKFLOW_ERROR: "Ошибка workflow",
            ModuleActivityType.REGION_PROCESSING: "Обработка региона",
            
            ModuleActivityType.DB_OPERATION: "Операция БД",
            ModuleActivityType.DB_ERROR: "Ошибка БД",
            
            ModuleActivityType.SYSTEM_STARTUP: "Система запущена",
            ModuleActivityType.SYSTEM_SHUTDOWN: "Система остановлена",
            ModuleActivityType.SYSTEM_HEALTH: "Проверка здоровья"
        }
        return display_names.get(activity_type, activity_type.value)
    
    def _format_details(self, details: Optional[Dict[str, Any]], activity_type: ModuleActivityType) -> Optional[Dict[str, Any]]:
        """Форматировать детали уведомления"""
        if not details:
            return None
        
        formatted = {}
        
        # Добавляем эмодзи в зависимости от типа активности
        if activity_type == ModuleActivityType.VK_POSTS_FOUND:
            formatted["📊 Найдено постов"] = details.get("posts_count", 0)
            formatted["🏢 Сообществ"] = details.get("communities_count", 0)
            formatted["⏱️ Время"] = details.get("duration", "N/A")
            
        elif activity_type == ModuleActivityType.AI_ANALYSIS_COMPLETED:
            formatted["📝 Проанализировано"] = details.get("analyzed_count", 0)
            formatted["🏷️ Категории"] = details.get("categories", [])
            formatted["⏱️ Время"] = details.get("duration", "N/A")
            
        elif activity_type == ModuleActivityType.FILTER_COMPLETED:
            formatted["📥 До фильтрации"] = details.get("before_count", 0)
            formatted["📤 После фильтрации"] = details.get("after_count", 0)
            formatted["❌ Отклонено"] = details.get("rejected_count", 0)
            formatted["📊 Процент отклонения"] = f"{details.get('rejection_rate', 0):.1f}%"
            
        elif activity_type == ModuleActivityType.PUBLISH_COMPLETED:
            formatted["📝 Post ID"] = details.get("post_id", "N/A")
            formatted["🔗 URL"] = details.get("post_url", "N/A")
            formatted["👥 Группа"] = details.get("group_id", "N/A")
            
        elif activity_type == ModuleActivityType.WORKFLOW_COMPLETED:
            formatted["🌍 Регионов обработано"] = details.get("regions_processed", 0)
            formatted["📊 Постов собрано"] = details.get("posts_collected", 0)
            formatted["✅ Постов принято"] = details.get("posts_accepted", 0)
            formatted["⏱️ Время выполнения"] = f"{details.get('duration', 0):.1f}s"
            
        else:
            # Для остальных типов просто копируем детали
            formatted = details
        
        return formatted
    
    def _update_module_stats(self, module_name: str, activity_type: ModuleActivityType):
        """Обновить статистику модуля"""
        if module_name not in self.module_stats:
            self.module_stats[module_name] = {
                "total_activities": 0,
                "last_activity": None,
                "activity_counts": {}
            }
        
        stats = self.module_stats[module_name]
        stats["total_activities"] += 1
        stats["last_activity"] = now_moscow()
        
        activity_key = activity_type.value
        if activity_key not in stats["activity_counts"]:
            stats["activity_counts"][activity_key] = 0
        stats["activity_counts"][activity_key] += 1
    
    def get_module_stats(self, module_name: str) -> Optional[Dict[str, Any]]:
        """Получить статистику модуля"""
        return self.module_stats.get(module_name)
    
    def get_all_module_stats(self) -> Dict[str, Any]:
        """Получить статистику всех модулей"""
        return self.module_stats
    
    def get_recent_activities(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Получить последние активности модулей"""
        activities = []
        
        for module_name, stats in self.module_stats.items():
            if stats["last_activity"]:
                activities.append({
                    "module": module_name,
                    "last_activity": stats["last_activity"],
                    "total_activities": stats["total_activities"],
                    "activity_counts": stats["activity_counts"]
                })
        
        # Сортируем по времени последней активности
        activities.sort(key=lambda x: x["last_activity"], reverse=True)
        
        return activities[:limit]


# Глобальный экземпляр
module_activity_notifier = ModuleActivityNotifier()


# Удобные функции для модулей
def notify_vk_scan_started(region: str, communities_count: int):
    """Уведомление о начале сканирования VK"""
    module_activity_notifier.notify_activity(
        module_name="vk_monitor",
        activity_type=ModuleActivityType.VK_SCAN_STARTED,
        message=f"Начато сканирование {communities_count} сообществ региона {region.upper()}",
        details={"communities_count": communities_count},
        region=region
    )


def notify_vk_scan_completed(region: str, posts_count: int, communities_count: int, duration: float):
    """Уведомление о завершении сканирования VK"""
    module_activity_notifier.notify_activity(
        module_name="vk_monitor",
        activity_type=ModuleActivityType.VK_SCAN_COMPLETED,
        message=f"Сканирование завершено: найдено {posts_count} постов",
        details={
            "posts_count": posts_count,
            "communities_count": communities_count,
            "duration": f"{duration:.1f}s"
        },
        region=region
    )


def notify_vk_posts_found(region: str, posts_count: int, community_name: str):
    """Уведомление о найденных постах"""
    module_activity_notifier.notify_activity(
        module_name="vk_monitor",
        activity_type=ModuleActivityType.VK_POSTS_FOUND,
        message=f"Найдено {posts_count} новых постов в сообществе {community_name}",
        details={"posts_count": posts_count, "community_name": community_name},
        region=region
    )


def notify_ai_analysis_started(posts_count: int):
    """Уведомление о начале AI анализа"""
    module_activity_notifier.notify_activity(
        module_name="ai_analyzer",
        activity_type=ModuleActivityType.AI_ANALYSIS_STARTED,
        message=f"Начат AI анализ {posts_count} постов",
        details={"posts_count": posts_count}
    )


def notify_ai_analysis_completed(analyzed_count: int, categories: List[str], duration: float):
    """Уведомление о завершении AI анализа"""
    module_activity_notifier.notify_activity(
        module_name="ai_analyzer",
        activity_type=ModuleActivityType.AI_ANALYSIS_COMPLETED,
        message=f"AI анализ завершен: проанализировано {analyzed_count} постов",
        details={
            "analyzed_count": analyzed_count,
            "categories": categories,
            "duration": f"{duration:.1f}s"
        }
    )


def notify_filter_started(posts_count: int):
    """Уведомление о начале фильтрации"""
    module_activity_notifier.notify_activity(
        module_name="filter_pipeline",
        activity_type=ModuleActivityType.FILTER_STARTED,
        message=f"Начата фильтрация {posts_count} постов",
        details={"posts_count": posts_count}
    )


def notify_filter_completed(before_count: int, after_count: int, rejected_count: int, rejection_rate: float):
    """Уведомление о завершении фильтрации"""
    module_activity_notifier.notify_activity(
        module_name="filter_pipeline",
        activity_type=ModuleActivityType.FILTER_COMPLETED,
        message=f"Фильтрация завершена: {after_count} из {before_count} постов прошли фильтры",
        details={
            "before_count": before_count,
            "after_count": after_count,
            "rejected_count": rejected_count,
            "rejection_rate": rejection_rate
        }
    )


def notify_publish_started(region: str, posts_count: int):
    """Уведомление о начале публикации"""
    module_activity_notifier.notify_activity(
        module_name="publisher",
        activity_type=ModuleActivityType.PUBLISH_STARTED,
        message=f"Начата публикация дайджеста из {posts_count} постов",
        details={"posts_count": posts_count},
        region=region
    )


def notify_publish_completed(post_id: int, post_url: str, group_id: int):
    """Уведомление о завершении публикации"""
    module_activity_notifier.notify_activity(
        module_name="publisher",
        activity_type=ModuleActivityType.PUBLISH_COMPLETED,
        message=f"Публикация завершена успешно",
        details={
            "post_id": post_id,
            "post_url": post_url,
            "group_id": group_id
        }
    )


def notify_workflow_started(regions: List[str]):
    """Уведомление о запуске workflow"""
    module_activity_notifier.notify_activity(
        module_name="workflow",
        activity_type=ModuleActivityType.WORKFLOW_STARTED,
        message=f"Запущен Production Workflow для регионов: {', '.join(regions)}",
        details={"regions": regions}
    )


def notify_workflow_completed(regions_processed: int, posts_collected: int, posts_accepted: int, duration: float):
    """Уведомление о завершении workflow"""
    module_activity_notifier.notify_activity(
        module_name="workflow",
        activity_type=ModuleActivityType.WORKFLOW_COMPLETED,
        message=f"Production Workflow завершен: обработано {regions_processed} регионов",
        details={
            "regions_processed": regions_processed,
            "posts_collected": posts_collected,
            "posts_accepted": posts_accepted,
            "duration": duration
        }
    )


def notify_region_processing(region: str, step: str):
    """Уведомление об обработке региона"""
    module_activity_notifier.notify_activity(
        module_name="workflow",
        activity_type=ModuleActivityType.REGION_PROCESSING,
        message=f"Обработка региона {region.upper()}: {step}",
        details={"step": step},
        region=region
    )


def notify_system_startup():
    """Уведомление о запуске системы"""
    module_activity_notifier.notify_activity(
        module_name="system",
        activity_type=ModuleActivityType.SYSTEM_STARTUP,
        message="Система SETKA запущена и готова к работе",
        details={"status": "ready"}
    )


def notify_system_health():
    """Уведомление о проверке здоровья системы"""
    module_activity_notifier.notify_activity(
        module_name="system",
        activity_type=ModuleActivityType.SYSTEM_HEALTH,
        message="Проверка здоровья системы выполнена",
        details={"status": "healthy"}
    )


if __name__ == "__main__":
    # Тестирование
    notify_system_startup()
    notify_vk_scan_started("mi", 5)
    notify_vk_posts_found("mi", 10, "Тестовое сообщество")
    notify_vk_scan_completed("mi", 10, 5, 15.5)
    notify_ai_analysis_started(10)
    notify_ai_analysis_completed(8, ["новости", "культура"], 5.2)
    notify_filter_started(10)
    notify_filter_completed(10, 7, 3, 30.0)
    notify_publish_started("mi", 5)
    notify_publish_completed(12345, "https://vk.com/wall-12345_12345", -12345)
    notify_workflow_started(["mi", "nolinsk"])
    notify_workflow_completed(2, 50, 35, 120.5)
    
    print("Module activity notifications sent!")
