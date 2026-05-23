"""
System Status Notifications - Уведомления о состоянии системы SETKA
"""

import asyncio
import logging
from enum import Enum
from typing import Dict, List, Optional

from config.runtime import PRODUCTION_WORKFLOW_CONFIG
from modules.celery_task_monitor import celery_task_monitor
from modules.service_notifications import (
    NotificationType,
    ServiceNotification,
    service_notifications,
)
from utils.timezone import get_moscow_hour, is_work_hours_moscow, now_moscow

logger = logging.getLogger(__name__)


class SystemStatusType(Enum):
    """Типы статусов системы"""

    WORKFLOW_ACTIVE = "workflow_active"
    WORKFLOW_PAUSED = "workflow_paused"
    WORKFLOW_STARTING = "workflow_starting"
    WORKFLOW_COMPLETED = "workflow_completed"
    SYSTEM_HEALTHY = "system_healthy"
    SYSTEM_WARNING = "system_warning"
    SYSTEM_ERROR = "system_error"
    REGION_PROCESSING = "region_processing"
    REGION_COMPLETED = "region_completed"
    MONITORING_ACTIVE = "monitoring_active"


class SystemStatusNotifier:
    """Уведомления о состоянии системы"""

    def __init__(self):
        self.last_status_check = None
        self.last_workflow_status = None
        self.last_task_activity_check = None
        self.status_history = []
        self.max_history = 100
        self.monitoring_task = None

    def add_status_notification(
        self,
        status_type: SystemStatusType,
        message: str,
        region: Optional[str] = None,
        details: Optional[Dict] = None,
    ):
        """Добавить уведомление о статусе"""
        # Создаем сервисное уведомление
        notification_type = (
            NotificationType.SUCCESS
            if "active" in status_type.value
            else NotificationType.SYSTEM_START
        )

        if status_type == SystemStatusType.SYSTEM_ERROR:
            notification_type = NotificationType.ERROR
        elif status_type == SystemStatusType.SYSTEM_WARNING:
            notification_type = NotificationType.ERROR

        service_notifications.add_notification(
            ServiceNotification(notification_type, message, region=region, details=details)
        )

        # Сохраняем в историю
        self.status_history.append(
            {
                "timestamp": now_moscow(),
                "type": status_type.value,
                "message": message,
                "region": region,
                "details": details,
            }
        )

        # Ограничиваем историю
        if len(self.status_history) > self.max_history:
            self.status_history = self.status_history[-self.max_history :]

    def check_workflow_status(self):
        """Проверить статус автоматической карусели"""
        current_hour = get_moscow_hour()
        work_hours_start = PRODUCTION_WORKFLOW_CONFIG.get("work_hours_start", 7)
        work_hours_end = PRODUCTION_WORKFLOW_CONFIG.get("work_hours_end", 22)

        is_work_hours = is_work_hours_moscow(work_hours_start, work_hours_end)

        # Определяем статус
        if is_work_hours:
            current_status = "active"
        else:
            current_status = "paused"

        # Проверяем, изменился ли статус
        if self.last_workflow_status != current_status:
            if current_status == "active":
                self.add_status_notification(
                    SystemStatusType.WORKFLOW_ACTIVE,
                    f"🔄 Автоматическая карусель АКТИВНА (рабочие часы: {work_hours_start}:00-{work_hours_end}:00 MSK)",
                    details={
                        "work_hours_start": work_hours_start,
                        "work_hours_end": work_hours_end,
                        "current_hour": current_hour,
                    },
                )
            else:
                self.add_status_notification(
                    SystemStatusType.WORKFLOW_PAUSED,
                    f"⏸️ Автоматическая карусель ПРИОСТАНОВЛЕНА (вне рабочих часов: {work_hours_start}:00-{work_hours_end}:00 MSK)",
                    details={
                        "work_hours_start": work_hours_start,
                        "work_hours_end": work_hours_end,
                        "current_hour": current_hour,
                        "next_start": f"{work_hours_start}:00 MSK",
                    },
                )

            self.last_workflow_status = current_status

    def add_monitoring_status(self):
        """Добавить статус мониторинга"""
        current_time = now_moscow()

        # Проверяем, прошло ли достаточно времени с последней проверки (10 минут)
        if (
            self.last_status_check and (current_time - self.last_status_check).total_seconds() < 600
        ):  # 10 минут
            return

        # Проверяем рабочие часы для более информативного сообщения
        work_hours_start = PRODUCTION_WORKFLOW_CONFIG.get("work_hours_start", 7)
        work_hours_end = PRODUCTION_WORKFLOW_CONFIG.get("work_hours_end", 22)
        is_work_hours = is_work_hours_moscow(work_hours_start, work_hours_end)

        if is_work_hours:
            message = f"👁️ Система мониторинга активна (время: {current_time.strftime('%H:%M MSK')}, рабочие часы)"
        else:
            message = f"👁️ Система мониторинга работает (время: {current_time.strftime('%H:%M MSK')}, вне рабочих часов)"

        self.add_status_notification(
            SystemStatusType.MONITORING_ACTIVE,
            message,
            details={
                "timestamp": current_time.isoformat(),
                "work_hours_active": is_work_hours,
                "work_hours": f"{work_hours_start}:00-{work_hours_end}:00 MSK",
            },
        )

        self.last_status_check = current_time

    def add_region_processing(self, region_code: str, status: str = "started"):
        """Добавить уведомление об обработке региона"""
        if status == "started":
            self.add_status_notification(
                SystemStatusType.REGION_PROCESSING,
                f"🏘️ Начинаю обработку региона {region_code.upper()}",
                region=region_code,
                details={"status": "started"},
            )
        elif status == "completed":
            self.add_status_notification(
                SystemStatusType.REGION_COMPLETED,
                f"✅ Регион {region_code.upper()} обработан успешно",
                region=region_code,
                details={"status": "completed"},
            )

    def add_system_health(self, health_status: str, details: Optional[Dict] = None):
        """Добавить уведомление о здоровье системы"""
        if health_status == "healthy":
            self.add_status_notification(
                SystemStatusType.SYSTEM_HEALTHY, "💚 Система работает нормально", details=details
            )
        elif health_status == "warning":
            self.add_status_notification(
                SystemStatusType.SYSTEM_WARNING,
                "⚠️ Предупреждение: система работает с ограничениями",
                details=details,
            )
        elif health_status == "error":
            self.add_status_notification(
                SystemStatusType.SYSTEM_ERROR, "❌ Ошибка в работе системы", details=details
            )

    def get_current_status_summary(self) -> Dict:
        """Получить текущий статус системы"""
        current_hour = get_moscow_hour()
        work_hours_start = PRODUCTION_WORKFLOW_CONFIG.get("work_hours_start", 7)
        work_hours_end = PRODUCTION_WORKFLOW_CONFIG.get("work_hours_end", 22)
        is_work_hours = is_work_hours_moscow(work_hours_start, work_hours_end)

        return {
            "workflow_status": "active" if is_work_hours else "paused",
            "current_hour": current_hour,
            "work_hours": f"{work_hours_start}:00-{work_hours_end}:00 MSK",
            "next_action": (
                f"Следующий запуск в {work_hours_start}:00 MSK"
                if not is_work_hours
                else "Каждый час в рабочее время"
            ),
            "last_check": self.last_status_check.isoformat() if self.last_status_check else None,
            "status_history_count": len(self.status_history),
        }

    def get_recent_status_notifications(self, limit: int = 20) -> List[Dict]:
        """Получить последние уведомления о статусе"""
        recent = self.status_history[-limit:] if self.status_history else []
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

    def add_task_activity_status(self):
        """Добавить статус активности задач"""
        try:
            current_time = now_moscow()

            # Проверяем, прошло ли достаточно времени с последней проверки активности задач (5 минут)
            if (
                self.last_task_activity_check
                and (current_time - self.last_task_activity_check).total_seconds() < 300
            ):
                return

            # Получаем статистику задач
            stats = celery_task_monitor.get_task_statistics()
            active_tasks = celery_task_monitor.get_active_tasks()
            recent_tasks = celery_task_monitor.get_recent_tasks(5)

            # Определяем статус системы
            if active_tasks:
                task_names = [task.get("task_name", "Unknown") for task in active_tasks]
                formatted_names = [self._format_task_name_for_user(name) for name in task_names[:3]]
                self.add_status_notification(
                    SystemStatusType.MONITORING_ACTIVE,
                    f"🔄 Выполняются задачи: {', '.join(formatted_names)}{'...' if len(task_names) > 3 else ''}",
                    details={
                        "active_tasks": len(active_tasks),
                        "task_names": task_names,
                        "statistics": stats,
                    },
                )
            else:
                # Проверяем последние задачи
                if recent_tasks:
                    last_task = recent_tasks[0]
                    task_name = last_task.get("task_name", "Unknown")
                    status = last_task.get("status", "unknown")

                    if status == "success":
                        formatted_name = self._format_task_name_for_user(task_name)
                        self.add_status_notification(
                            SystemStatusType.MONITORING_ACTIVE,
                            f"✅ Последняя задача завершена: {formatted_name}",
                            details={"last_task": last_task, "statistics": stats},
                        )
                    elif status == "failure":
                        formatted_name = self._format_task_name_for_user(task_name)
                        self.add_status_notification(
                            SystemStatusType.SYSTEM_ERROR,
                            f"❌ Ошибка в задаче: {formatted_name}",
                            details={"last_task": last_task, "statistics": stats},
                        )
                else:
                    # Проверяем, есть ли запланированные задачи
                    scheduled_tasks = celery_task_monitor.get_scheduled_tasks()
                    if scheduled_tasks:
                        self.add_status_notification(
                            SystemStatusType.MONITORING_ACTIVE,
                            f"⏳ Система ожидает выполнения задач ({len(scheduled_tasks)} запланировано)",
                            details={"scheduled_tasks": len(scheduled_tasks), "statistics": stats},
                        )
                    else:
                        # Проверяем рабочие часы
                        current_hour = get_moscow_hour()
                        work_hours_start = PRODUCTION_WORKFLOW_CONFIG.get("work_hours_start", 7)
                        work_hours_end = PRODUCTION_WORKFLOW_CONFIG.get("work_hours_end", 22)

                        if is_work_hours_moscow(work_hours_start, work_hours_end):
                            self.add_status_notification(
                                SystemStatusType.MONITORING_ACTIVE,
                                f"💤 Система в режиме ожидания (рабочие часы: {work_hours_start}:00-{work_hours_end}:00 MSK)",
                                details={
                                    "work_hours_active": True,
                                    "current_hour": current_hour,
                                    "statistics": stats,
                                },
                            )
                        else:
                            self.add_status_notification(
                                SystemStatusType.MONITORING_ACTIVE,
                                f"😴 Система отдыхает (вне рабочих часов: {work_hours_start}:00-{work_hours_end}:00 MSK)",
                                details={
                                    "work_hours_active": False,
                                    "current_hour": current_hour,
                                    "next_start": f"{work_hours_start}:00 MSK",
                                    "statistics": stats,
                                },
                            )

            # Обновляем время последней проверки активности задач
            self.last_task_activity_check = current_time

        except Exception as e:
            logger.error(f"Error adding task activity status: {e}")
            self.add_status_notification(
                SystemStatusType.SYSTEM_ERROR,
                f"❌ Ошибка мониторинга задач: {e}",
                details={"error": str(e)},
            )

    def _format_task_name_for_user(self, task_name: str) -> str:
        """Форматировать название задачи для пользователя"""
        name_mapping = {
            "tasks.production_workflow_tasks.run_production_workflow_all_regions": "Автоматическая карусель",
            "tasks.production_workflow_tasks.test_simple_task": "Тестовая задача",
            "tasks.monitoring_tasks.health_check": "Проверка системы",
            "tasks.monitoring_tasks.scan_region": "Сканирование региона",
            "tasks.notification_tasks.check_vk_notifications": "Проверка уведомлений VK",
            "tasks.analysis_tasks.analyze_new_posts": "Анализ постов",
            "tasks.publishing_tasks.publish_post": "Публикация поста",
            "tasks.real_vk_workflow.collect_and_publish_test": "Тест VK workflow",
        }

        return name_mapping.get(task_name, task_name.split(".")[-1].replace("_", " ").title())

    def add_service_activity_status(self):
        """Добавить статус активности сервисов (новая система)"""
        try:
            from modules.service_activity_notifier import service_activity_notifier

            # Получаем статус от сервисов
            status_summary = service_activity_notifier.get_system_status_summary()
            active_operations = service_activity_notifier.get_active_operations()

            # Определяем сообщение на основе статуса
            if status_summary["status"] == "active":
                # Есть активные операции
                operation_names = []
                for op_id, op_data in active_operations.items():
                    op_type = op_data.get("type", "unknown")
                    region = op_data.get("region", "Unknown")

                    # Добавляем специальную иконку для круглосуточных регионов
                    region_icon = ""
                    if region.lower() in ["тест-инфо", "test-info", "тест инфо"]:
                        region_icon = "🌙 "

                    if op_type == "post_collection":
                        operation_names.append(f"{region_icon}Сбор постов в {region}")
                    elif op_type == "post_sorting":
                        operation_names.append(f"{region_icon}Сортировка постов в {region}")
                    elif op_type == "digest_creation":
                        operation_names.append(f"{region_icon}Создание дайджеста для {region}")
                    elif op_type == "digest_publishing":
                        operation_names.append(f"{region_icon}Публикация дайджеста в {region}")
                    else:
                        operation_names.append(f"{region_icon}Операция в {region}")

                message = f"🔄 Выполняются операции: {', '.join(operation_names[:2])}{'...' if len(operation_names) > 2 else ''}"

                self.add_status_notification(
                    SystemStatusType.MONITORING_ACTIVE,
                    message,
                    details={
                        "active_operations_count": len(active_operations),
                        "operation_types": list(
                            set(op["type"] for op in active_operations.values())
                        ),
                        "status_summary": status_summary,
                    },
                )
            else:
                # Система в режиме ожидания
                current_hour = get_moscow_hour()
                work_hours_start = PRODUCTION_WORKFLOW_CONFIG.get("work_hours_start", 7)
                work_hours_end = PRODUCTION_WORKFLOW_CONFIG.get("work_hours_end", 22)

                # Проверяем, есть ли круглосуточные регионы
                has_24h_regions = any(
                    region.lower() in ["тест-инфо", "test-info", "тест инфо"]
                    for region in ["Тест-Инфо"]  # Список круглосуточных регионов
                )

                if has_24h_regions:
                    if is_work_hours_moscow(work_hours_start, work_hours_end):
                        message = f"💤 Система в режиме ожидания (рабочие часы: {work_hours_start}:00-{work_hours_end}:00 MSK, 🌙 Тест-Инфо работает круглосуточно)"
                    else:
                        message = f"🌙 Система работает только для Тест-Инфо (вне рабочих часов: {work_hours_start}:00-{work_hours_end}:00 MSK)"
                else:
                    if is_work_hours_moscow(work_hours_start, work_hours_end):
                        message = f"💤 Система в режиме ожидания (рабочие часы: {work_hours_start}:00-{work_hours_end}:00 MSK)"
                    else:
                        message = f"😴 Система отдыхает (вне рабочих часов: {work_hours_start}:00-{work_hours_end}:00 MSK)"

                self.add_status_notification(
                    SystemStatusType.MONITORING_ACTIVE,
                    message,
                    details={
                        "work_hours_active": is_work_hours_moscow(work_hours_start, work_hours_end),
                        "current_hour": current_hour,
                        "status_summary": status_summary,
                    },
                )

        except Exception as e:
            logger.error(f"Error adding service activity status: {e}")
            self.add_status_notification(
                SystemStatusType.SYSTEM_ERROR,
                f"❌ Ошибка мониторинга сервисов: {e}",
                details={"error": str(e)},
            )

    async def _monitor_status(self):
        """Мониторинг статуса системы"""
        while True:
            try:
                # Проверяем статус workflow
                self.check_workflow_status()

                # Добавляем статус мониторинга (каждые 10 минут)
                self.add_monitoring_status()

                # Новая система: получаем статус от сервисов напрямую
                self.add_service_activity_status()

                # Ждем 5 минут между проверками
                await asyncio.sleep(300)

            except Exception as e:
                logger.error(f"Error in status monitoring: {e}")
                await asyncio.sleep(300)

    def get_detailed_system_status(self) -> Dict:
        """Получить детальный статус системы с информацией о задачах"""
        try:
            # Получаем базовый статус
            base_status = self.get_current_status_summary()

            # Получаем информацию о задачах
            task_stats = celery_task_monitor.get_task_statistics()
            active_tasks = celery_task_monitor.get_active_tasks()
            recent_tasks = celery_task_monitor.get_recent_tasks(10)
            scheduled_tasks = celery_task_monitor.get_scheduled_tasks()

            # Форматируем задачи для отображения
            formatted_active = [
                celery_task_monitor.format_task_for_display(task) for task in active_tasks
            ]
            formatted_recent = [
                celery_task_monitor.format_task_for_display(task) for task in recent_tasks
            ]
            formatted_scheduled = [
                celery_task_monitor.format_task_for_display(task) for task in scheduled_tasks
            ]

            return {
                **base_status,
                "task_activity": {
                    "active_tasks": {"tasks": formatted_active, "count": len(formatted_active)},
                    "recent_tasks": {"tasks": formatted_recent, "count": len(formatted_recent)},
                    "scheduled_tasks": {
                        "tasks": formatted_scheduled,
                        "count": len(formatted_scheduled),
                    },
                    "statistics": task_stats,
                },
                "timestamp": now_moscow().isoformat(),
            }

        except Exception as e:
            logger.error(f"Error getting detailed system status: {e}")
            return {
                **self.get_current_status_summary(),
                "task_activity": {
                    "error": str(e),
                    "active_tasks": {"tasks": [], "count": 0},
                    "recent_tasks": {"tasks": [], "count": 0},
                    "scheduled_tasks": {"tasks": [], "count": 0},
                    "statistics": {},
                },
                "timestamp": now_moscow().isoformat(),
            }


# Глобальный экземпляр
system_status_notifier = SystemStatusNotifier()


async def start_status_monitoring():
    """Запустить мониторинг статуса системы"""
    logger.info("Starting system status monitoring...")

    # Добавляем начальное уведомление
    system_status_notifier.add_system_health(
        "healthy",
        {"message": "Система запущена и готова к работе", "timestamp": now_moscow().isoformat()},
    )

    # Проверяем статус карусели
    system_status_notifier.check_workflow_status()

    # Добавляем статус мониторинга
    system_status_notifier.add_monitoring_status()

    # Добавляем статус активности задач
    system_status_notifier.add_task_activity_status()

    # Запускаем фоновый мониторинг
    if system_status_notifier.monitoring_task is None:
        system_status_notifier.monitoring_task = asyncio.create_task(
            system_status_notifier._monitor_status()
        )

    logger.info("System status monitoring started")


if __name__ == "__main__":
    # Тестирование
    async def test():
        await start_status_monitoring()

        # Проверяем статус
        summary = system_status_notifier.get_current_status_summary()
        print("Status summary:", summary)

        # Получаем уведомления
        notifications = system_status_notifier.get_recent_status_notifications(5)
        print("Recent notifications:", notifications)

    asyncio.run(test())
