"""
Celery application for SETKA project
Handles background tasks: monitoring, analysis, publishing
"""
import os
from celery import Celery
from celery.schedules import crontab
from config.config_secure import REDIS

# Initialize Celery app
app = Celery(
    'setka',
    broker=f'redis://{REDIS["host"]}:{REDIS["port"]}/{REDIS["db"]}',
    backend=f'redis://{REDIS["host"]}:{REDIS["port"]}/{REDIS["db"]}',
    include=[
        'tasks.monitoring_tasks',
        'tasks.analysis_tasks',
        'tasks.publishing_tasks'
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
    # VK Monitoring - every 5 minutes
    'monitor-vk-communities': {
        'task': 'tasks.monitoring_tasks.scan_all_communities',
        'schedule': 300.0,  # 5 minutes
        'options': {
            'expires': 240,
        }
    },
    
    # AI Analysis - every 2 minutes
    'analyze-new-posts': {
        'task': 'tasks.analysis_tasks.analyze_new_posts',
        'schedule': 120.0,  # 2 minutes
        'options': {
            'expires': 100,
        }
    },
    
    # Publishing - every hour at minute 5
    'publish-approved-posts': {
        'task': 'tasks.publishing_tasks.publish_scheduled_posts',
        'schedule': crontab(minute='5'),
        'options': {
            'expires': 3000,
        }
    },
    
    # Health check - every minute
    'health-check': {
        'task': 'tasks.monitoring_tasks.health_check',
        'schedule': 60.0,  # 1 minute
        'options': {
            'expires': 50,
        }
    },
    
    # Cleanup old results - daily at 3:30 AM
    'cleanup-old-data': {
        'task': 'tasks.monitoring_tasks.cleanup_old_data',
        'schedule': crontab(hour=3, minute=30),
    },
}

if __name__ == '__main__':
    app.start()

