"""
Celery Task Monitor
Отслеживает выполнение задач Celery и предоставляет информацию о статусе
"""
import logging
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from enum import Enum
import redis
from celery import Celery
from celery.result import AsyncResult

from config.runtime import REDIS
from utils.timezone import now_moscow, format_moscow_time

logger = logging.getLogger(__name__)

class TaskStatus(Enum):
    PENDING = "pending"
    STARTED = "started"
    SUCCESS = "success"
    FAILURE = "failure"
    RETRY = "retry"
    REVOKED = "revoked"

class TaskType(Enum):
    PRODUCTION_WORKFLOW = "production_workflow"
    MONITORING = "monitoring"
    ANALYSIS = "analysis"
    PUBLISHING = "publishing"
    NOTIFICATION = "notification"
    HEALTH_CHECK = "health_check"
    TEST = "test"

class CeleryTaskMonitor:
    """Монитор задач Celery"""
    
    def __init__(self):
        self.redis_client = redis.Redis(
            host=REDIS["host"],
            port=REDIS["port"],
            db=REDIS["db"],
            decode_responses=True
        )
        self.task_history: List[Dict] = []
        self.max_history = 100
        
    def get_task_info(self, task_id: str) -> Optional[Dict]:
        """Получить информацию о задаче по ID"""
        try:
            # Получаем результат из Redis
            result_key = f"celery-task-meta-{task_id}"
            result_data = self.redis_client.get(result_key)
            
            if not result_data:
                return None
                
            result = json.loads(result_data)
            
            # Определяем тип задачи по имени
            task_name = result.get('task', '')
            task_type = self._get_task_type(task_name)
            
            # Определяем статус
            status = result.get('status', 'PENDING')
            if status == 'SUCCESS':
                status = TaskStatus.SUCCESS
            elif status == 'FAILURE':
                status = TaskStatus.FAILURE
            else:
                status = TaskStatus.PENDING
                
            return {
                'task_id': task_id,
                'task_name': task_name,
                'task_type': task_type.value,
                'status': status.value,
                'result': result.get('result'),
                'error': result.get('result', {}).get('exc_message') if status == TaskStatus.FAILURE else None,
                'date_done': result.get('date_done'),
                'timestamp': now_moscow().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error getting task info for {task_id}: {e}")
            return None
    
    def _get_task_type(self, task_name: str) -> TaskType:
        """Определить тип задачи по имени"""
        if 'production_workflow' in task_name:
            return TaskType.PRODUCTION_WORKFLOW
        elif 'monitoring' in task_name:
            return TaskType.MONITORING
        elif 'analysis' in task_name:
            return TaskType.ANALYSIS
        elif 'publishing' in task_name:
            return TaskType.PUBLISHING
        elif 'notification' in task_name:
            return TaskType.NOTIFICATION
        elif 'health_check' in task_name:
            return TaskType.HEALTH_CHECK
        elif 'test' in task_name:
            return TaskType.TEST
        else:
            return TaskType.MONITORING
    
    def get_recent_tasks(self, limit: int = 20) -> List[Dict]:
        """Получить недавние задачи"""
        try:
            # Получаем все ключи задач из Redis
            task_keys = self.redis_client.keys("celery-task-meta-*")
            
            tasks = []
            for key in task_keys:
                task_id = key.replace("celery-task-meta-", "")
                task_info = self.get_task_info(task_id)
                if task_info:
                    tasks.append(task_info)
            
            # Сортируем по времени завершения (новые сначала)
            tasks.sort(key=lambda x: x.get('date_done', ''), reverse=True)
            
            return tasks[:limit]
            
        except Exception as e:
            logger.error(f"Error getting recent tasks: {e}")
            return []
    
    def get_active_tasks(self) -> List[Dict]:
        """Получить активные задачи"""
        try:
            # Получаем активные задачи из Celery
            from celery_app import app
            
            # Используем inspect для получения активных задач
            inspect = app.control.inspect()
            active_tasks = inspect.active()
            
            if not active_tasks:
                return []
                
            tasks = []
            for worker, worker_tasks in active_tasks.items():
                for task in worker_tasks:
                    task_info = {
                        'task_id': task['id'],
                        'task_name': task['name'],
                        'task_type': self._get_task_type(task['name']).value,
                        'status': TaskStatus.STARTED.value,
                        'worker': worker,
                        'args': task.get('args', []),
                        'kwargs': task.get('kwargs', {}),
                        'time_start': task.get('time_start'),
                        'timestamp': now_moscow().isoformat()
                    }
                    tasks.append(task_info)
            
            return tasks
            
        except Exception as e:
            logger.error(f"Error getting active tasks: {e}")
            return []
    
    def get_scheduled_tasks(self) -> List[Dict]:
        """Получить запланированные задачи"""
        try:
            from celery_app import app
            
            # Получаем запланированные задачи из Celery
            inspect = app.control.inspect()
            scheduled_tasks = inspect.scheduled()
            
            if not scheduled_tasks:
                return []
                
            tasks = []
            for worker, worker_tasks in scheduled_tasks.items():
                for task in worker_tasks:
                    task_info = {
                        'task_id': task['id'],
                        'task_name': task['name'],
                        'task_type': self._get_task_type(task['name']).value,
                        'status': TaskStatus.PENDING.value,
                        'worker': worker,
                        'eta': task.get('eta'),
                        'args': task.get('args', []),
                        'kwargs': task.get('kwargs', {}),
                        'timestamp': now_moscow().isoformat()
                    }
                    tasks.append(task_info)
            
            return tasks
            
        except Exception as e:
            logger.error(f"Error getting scheduled tasks: {e}")
            return []
    
    def get_task_statistics(self) -> Dict:
        """Получить статистику задач"""
        try:
            recent_tasks = self.get_recent_tasks(50)
            active_tasks = self.get_active_tasks()
            scheduled_tasks = self.get_scheduled_tasks()
            
            # Подсчитываем статистику
            stats = {
                'total_recent': len(recent_tasks),
                'total_active': len(active_tasks),
                'total_scheduled': len(scheduled_tasks),
                'success_count': len([t for t in recent_tasks if t['status'] == TaskStatus.SUCCESS.value]),
                'failure_count': len([t for t in recent_tasks if t['status'] == TaskStatus.FAILURE.value]),
                'task_types': {}
            }
            
            # Статистика по типам задач
            all_tasks = recent_tasks + active_tasks + scheduled_tasks
            for task in all_tasks:
                task_type = task['task_type']
                if task_type not in stats['task_types']:
                    stats['task_types'][task_type] = 0
                stats['task_types'][task_type] += 1
            
            return stats
            
        except Exception as e:
            logger.error(f"Error getting task statistics: {e}")
            return {}
    
    def format_task_for_display(self, task: Dict) -> Dict:
        """Форматировать задачу для отображения"""
        try:
            # Определяем иконку и цвет по типу задачи
            task_type = task.get('task_type', 'monitoring')
            status = task.get('status', 'pending')
            
            icons = {
                'production_workflow': '🔄',
                'monitoring': '👁️',
                'analysis': '📊',
                'publishing': '📝',
                'notification': '🔔',
                'health_check': '💚',
                'test': '🧪'
            }
            
            colors = {
                'success': '💚',
                'failure': '❌',
                'started': '🟡',
                'pending': '⏳',
                'retry': '🔄'
            }
            
            icon = icons.get(task_type, '📋')
            color = colors.get(status, '⚪')
            
            # Форматируем время
            date_done = task.get('date_done')
            if date_done:
                try:
                    dt = datetime.fromisoformat(date_done.replace('Z', '+00:00'))
                    formatted_time = format_moscow_time(dt)
                except (ValueError, AttributeError, TypeError) as e:
                    logger.warning(f"Failed to parse date_done '{date_done}': {e}")
                    formatted_time = date_done
            else:
                formatted_time = "В процессе"
            
            # Форматируем название задачи
            task_name = task.get('task_name', 'Unknown')
            display_name = self._format_task_name(task_name)
            
            return {
                'id': task.get('task_id', ''),
                'name': display_name,
                'type': task_type,
                'status': status,
                'icon': icon,
                'color': color,
                'time': formatted_time,
                'details': self._get_task_details(task),
                'timestamp': task.get('timestamp', now_moscow().isoformat())
            }
            
        except Exception as e:
            logger.error(f"Error formatting task for display: {e}")
            return task
    
    def _format_task_name(self, task_name: str) -> str:
        """Форматировать название задачи для отображения"""
        name_mapping = {
            'tasks.production_workflow_tasks.run_production_workflow_all_regions': 'Автоматическая карусель обработки',
            'tasks.production_workflow_tasks.test_simple_task': 'Тестовая задача',
            'tasks.monitoring_tasks.health_check': 'Проверка здоровья системы',
            'tasks.monitoring_tasks.scan_region': 'Сканирование региона',
            'tasks.notification_tasks.check_vk_notifications': 'Проверка уведомлений VK',
            'tasks.analysis_tasks.analyze_new_posts': 'Анализ новых постов',
            'tasks.publishing_tasks.publish_post': 'Публикация поста'
        }
        
        return name_mapping.get(task_name, task_name.split('.')[-1])
    
    def _get_task_details(self, task: Dict) -> Dict:
        """Получить детали задачи"""
        details = {}
        
        # Добавляем результат для успешных задач
        if task.get('status') == TaskStatus.SUCCESS.value:
            result = task.get('result')
            if isinstance(result, dict):
                details.update(result)
        
        # Добавляем ошибку для неудачных задач
        if task.get('status') == TaskStatus.FAILURE.value:
            error = task.get('error')
            if error:
                details['error'] = error
        
        # Добавляем информацию о воркере для активных задач
        if task.get('status') == TaskStatus.STARTED.value:
            worker = task.get('worker')
            if worker:
                details['worker'] = worker
        
        return details

# Глобальный экземпляр монитора
celery_task_monitor = CeleryTaskMonitor()
