"""
Celery Configuration

Конфигурация для Celery worker и beat scheduler
"""

# Broker и backend (Redis)
broker_url = "redis://localhost:6379/0"
result_backend = "redis://localhost:6379/0"

# Timezone
timezone = "Europe/Moscow"
enable_utc = False

# Task settings
task_serializer = "json"
accept_content = ["json"]
result_serializer = "json"

# Worker settings
worker_prefetch_multiplier = 1
# Переработка дочернего процесса пула. Было 1000 — и это, а не «опция не задана»,
# оказалось настоящим диагнозом OOM-убийств (найдено 2026-09-14).
#
# **Замер на проде, а не оценка.** Воркер завершает 250–380 задач в час (среднее
# ~300, считано по строкам «succeeded in» за сутки), ядро одно → concurrency 1 →
# дочерний процесс ровно один. При пороге 1000 он переживает **3.3 часа**, а
# ядро убивает его раньше: по `dmesg` восемь OOM-убийств за 09.09–14.09, RSS в
# момент смерти 390–518 МБ при 1536 МБ на боксе и **swap = 0**. Свежий ребёнок
# занимает ~87 МБ, то есть память копится до потолка внутри одного интервала
# переработки — порог просто не успевает сработать.
#
# 250 задач ≈ 50 минут при нынешнем темпе: ребёнок перерабатывается втрое чаще,
# чем растёт до опасного размера. Цена — форк с ре-импортом приложения примерно
# раз в час на 7000 задач в сутки; на фоне 1 ядра это шум.
#
# Почему не через `--max-tasks-per-child` в ExecStart юнита: конфиг уже здесь и
# уже применяется (`app.config_from_object("config.celery_config")`). Вторая
# копия настройки в юните на проде создала бы два места правды, и первое же
# расхождение искали бы в коде, а не в systemd.
#
# Это лечение накопления, а не его причины. Течь (что именно копит память) не
# найдена — порог лишь не даёт ей дорасти до убийства. См. PENDING.
worker_max_tasks_per_child = 250

# Task result settings
result_expires = 3600  # 1 час

# Task execution
task_acks_late = True
task_reject_on_worker_lost = True

# Logging
worker_log_format = "[%(asctime)s: %(levelname)s/%(processName)s] %(message)s"
worker_task_log_format = (
    "[%(asctime)s: %(levelname)s/%(processName)s] [%(task_name)s(%(task_id)s)] %(message)s"
)
