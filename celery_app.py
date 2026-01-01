"""
Celery application for SETKA project
Handles background tasks: monitoring, analysis, publishing
"""
import os
from celery import Celery
from celery.schedules import crontab
from config.runtime import REDIS

# Initialize Celery app
app = Celery(
    'setka',
    broker=f'redis://{REDIS["host"]}:{REDIS["port"]}/{REDIS["db"]}',
    backend=f'redis://{REDIS["host"]}:{REDIS["port"]}/{REDIS["db"]}',
    include=[
        'tasks.monitoring_tasks',
        'tasks.analysis_tasks',
        'tasks.publishing_tasks',
        'tasks.notification_tasks',  # NEW: Notifications monitoring
        'tasks.production_workflow_tasks',  # OLD: Production workflow automation
        'tasks.correct_workflow_tasks',  # NEW: Correct workflow with proper logic
        'tasks.real_vk_workflow',  # NEW: Real VK workflow
        'tasks.test_info_tasks'  # NEW: Test-Info 24/7 scheduler
    ]
)

# Celery configuration
app.conf.update(
    # Task settings
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='Europe/Moscow',
    enable_utc=True,
    
    # Task execution settings
    task_track_started=True,
    task_time_limit=300,  # 5 minutes max
    task_soft_time_limit=240,  # 4 minutes soft limit
    
    # Worker settings
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=100,
    
    # Result backend settings
    result_expires=3600,  # Results expire after 1 hour
    result_backend_transport_options={
        'master_name': 'mymaster',
    },
    
    # Retry settings
    task_acks_late=True,
    task_reject_on_worker_lost=True,
)

# Periodic task schedule
app.conf.beat_schedule = {
    # === CORRECT WORKFLOW (новая правильная логика) ===
    'correct-workflow': {
        'task': 'tasks.correct_workflow_tasks.run_correct_workflow',
        'schedule': crontab(minute=0, hour='7-22'),  # Каждый час с 7:00 до 22:00 MSK
        'options': {
            'expires': 3400,  # ~55 минут на выполнение
        }
    },
    
    # === PRODUCTION WORKFLOW (старая логика - отключена) ===
    # 'production-workflow-carousel': {
    #     'task': 'tasks.production_workflow_tasks.run_production_workflow_all_regions_sync',
    #     'schedule': crontab(minute=0, hour='7-22'),  # Каждый час с 7:00 до 22:00 MSK
    #     'options': {
    #         'expires': 3400,  # ~55 минут на выполнение
    #     }
    # },
    
    # === TEST TASK (для отладки) ===
    'test-simple-task': {
        'task': 'tasks.production_workflow_tasks.test_simple_task',
        'schedule': 60.0,  # Каждую минуту для тестирования
        'options': {
            'expires': 50,
        }
    },
    
    # === REAL VK WORKFLOW (ОТКЛЮЧЕНО - использует неправильную логику) ===
    # 'real-vk-test': {
    #     'task': 'tasks.real_vk_workflow.collect_and_publish_test',
    #     'schedule': 300.0,  # Каждые 5 минут для тестирования
    #     'options': {
    #         'expires': 240,
    #     }
    # },
    
    # === ТЕСТ-ИНФО (ОТКЛЮЧЕНО - использует неправильную логику) ===
    # 'test-info-schedule': {
    #     'task': 'tasks.test_info_tasks.execute_test_info_schedule',
    #     'schedule': 300.0,  # Каждые 5 минут круглосуточно
    #     'options': {
    #         'expires': 240,  # ~4 минуты на выполнение
    #     }
    # },
    
    # === МОНИТОРИНГ (оставить) ===
    'health-check': {
        'task': 'tasks.monitoring_tasks.health_check',
        'schedule': 300.0,  # Каждые 5 минут (было 1 минута - слишком часто)
        'options': {
            'expires': 240,
        }
    },
    
    'check-vk-notifications': {
        'task': 'tasks.notification_tasks.check_vk_notifications',
        'schedule': 3600.0,  # Каждый час (оставить как есть)
        'options': {
            'expires': 3500,
        }
    },
    
    # === ОБСЛУЖИВАНИЕ (оставить) ===
    'cleanup-old-data': {
        'task': 'tasks.monitoring_tasks.cleanup_old_data',
        'schedule': crontab(hour=3, minute=30),  # Ежедневно в 3:30
    },
    
# === УДАЛЕННЫЕ ЗАДАЧИ (дублировались с production workflow) ===
# 'monitor-vk-communities' - УДАЛЕНО (заменено на production-workflow)
# 'analyze-new-posts' - УДАЛЕНО (заменено на production-workflow)  
# 'publish-approved-posts' - УДАЛЕНО (заменено на production-workflow)
}

# === ЯВНАЯ РЕГИСТРАЦИЯ ЗАДАЧ ===
# Задача correct-workflow регистрируется автоматически через include

if __name__ == '__main__':
    app.start()

