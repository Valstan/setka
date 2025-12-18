"""
Celery Configuration

Конфигурация для Celery worker и beat scheduler
"""

# Broker и backend (Redis)
broker_url = 'redis://localhost:6379/0'
result_backend = 'redis://localhost:6379/0'

# Timezone
timezone = 'Europe/Moscow'
enable_utc = False

# Task settings
task_serializer = 'json'
accept_content = ['json']
result_serializer = 'json'

# Worker settings
worker_prefetch_multiplier = 1
worker_max_tasks_per_child = 1000

# Task result settings
result_expires = 3600  # 1 час

# Task execution
task_acks_late = True
task_reject_on_worker_lost = True

# Logging
worker_log_format = '[%(asctime)s: %(levelname)s/%(processName)s] %(message)s'
worker_task_log_format = '[%(asctime)s: %(levelname)s/%(processName)s] [%(task_name)s(%(task_id)s)] %(message)s'

