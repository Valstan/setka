"""Задача scan_suggested_ads зарегистрирована и есть beat-расписание."""

from __future__ import annotations


def test_scan_task_registered():
    from tasks.celery_app import app, scan_suggested_ads  # noqa: F401

    assert "tasks.celery_app.scan_suggested_ads" in app.tasks
    assert "scan-suggested-ads" in app.conf.beat_schedule
    entry = app.conf.beat_schedule["scan-suggested-ads"]
    assert entry["task"] == "tasks.celery_app.scan_suggested_ads"


def test_dm_scan_task_registered():
    from tasks.celery_app import app, scan_inbound_dm_ads  # noqa: F401

    assert "tasks.celery_app.scan_inbound_dm_ads" in app.tasks
    assert "scan-inbound-dm-ads" in app.conf.beat_schedule
    entry = app.conf.beat_schedule["scan-inbound-dm-ads"]
    assert entry["task"] == "tasks.celery_app.scan_inbound_dm_ads"


def test_reconcile_publications_task_registered():
    from tasks.celery_app import app, reconcile_scheduled_publications  # noqa: F401

    assert "tasks.celery_app.reconcile_scheduled_publications" in app.tasks
    assert "reconcile-scheduled-publications" in app.conf.beat_schedule
    entry = app.conf.beat_schedule["reconcile-scheduled-publications"]
    assert entry["task"] == "tasks.celery_app.reconcile_scheduled_publications"


def test_ad_repost_dispatch_registered_every_minute():
    """Диспетчер планировщика предложки: поминутный beat + watchdog (Этап 0)."""
    from tasks.celery_app import app, check_ad_repost_heartbeat, dispatch_ad_reposts  # noqa: F401

    assert "tasks.celery_app.dispatch_ad_reposts" in app.tasks
    assert "tasks.celery_app.check_ad_repost_heartbeat" in app.tasks
    entry = app.conf.beat_schedule["ad-repost-dispatch"]
    assert entry["task"] == "tasks.celery_app.dispatch_ad_reposts"
    assert entry["options"]["expires"] <= 60  # тик минутный — устаревший не копится
    wd = app.conf.beat_schedule["ad-repost-watchdog"]
    assert wd["task"] == "tasks.celery_app.check_ad_repost_heartbeat"


def test_ad_pending_watch_registered_hourly():
    """Сторож pending с прошедшей датой (аудит 2026-09-05): задача + hourly beat."""
    from tasks.celery_app import app, watch_ad_pending  # noqa: F401

    assert "tasks.celery_app.watch_ad_pending" in app.tasks
    entry = app.conf.beat_schedule["ad-pending-watch"]
    assert entry["task"] == "tasks.celery_app.watch_ad_pending"


def test_expire_ad_posts_task_registered():
    """Авто-снятие постов по сроку (С2) зарегистрировано + ежедневный beat."""
    from tasks.celery_app import app, expire_ad_posts  # noqa: F401

    assert "tasks.celery_app.expire_ad_posts" in app.tasks
    assert "expire-ad-posts-daily" in app.conf.beat_schedule
    entry = app.conf.beat_schedule["expire-ad-posts-daily"]
    assert entry["task"] == "tasks.celery_app.expire_ad_posts"


def test_collect_ad_stats_task_registered():
    """Суточный сбор метрик публикаций (С3) зарегистрирован + beat."""
    from tasks.celery_app import app, collect_ad_publication_stats  # noqa: F401

    assert "tasks.celery_app.collect_ad_publication_stats" in app.tasks
    assert "collect-ad-publication-stats-daily" in app.conf.beat_schedule
    entry = app.conf.beat_schedule["collect-ad-publication-stats-daily"]
    assert entry["task"] == "tasks.celery_app.collect_ad_publication_stats"


def test_alert_ad_debtors_task_registered():
    """Суточное напоминание о должниках (С4) зарегистрировано + beat."""
    from tasks.celery_app import alert_ad_debtors, app  # noqa: F401

    assert "tasks.celery_app.alert_ad_debtors" in app.tasks
    assert "alert-ad-debtors-daily" in app.conf.beat_schedule
    entry = app.conf.beat_schedule["alert-ad-debtors-daily"]
    assert entry["task"] == "tasks.celery_app.alert_ad_debtors"


def test_alert_ad_overspent_task_registered():
    """Суточное напоминание о перерасходе пакета (И2) зарегистрировано + beat."""
    from tasks.celery_app import alert_ad_overspent, app  # noqa: F401

    assert "tasks.celery_app.alert_ad_overspent" in app.tasks
    assert "alert-ad-overspent-daily" in app.conf.beat_schedule
    entry = app.conf.beat_schedule["alert-ad-overspent-daily"]
    assert entry["task"] == "tasks.celery_app.alert_ad_overspent"


def test_auto_greet_task_registered():
    """Авто-приветствие рекламодателю зарегистрировано + beat."""
    from tasks.celery_app import app, auto_greet_ad_requests  # noqa: F401

    assert "tasks.celery_app.auto_greet_ad_requests" in app.tasks
    assert "auto-greet-ad-requests" in app.conf.beat_schedule
    entry = app.conf.beat_schedule["auto-greet-ad-requests"]
    assert entry["task"] == "tasks.celery_app.auto_greet_ad_requests"


def test_prune_gateway_requests_task_registered():
    """Ретеншн лога VK-шлюза зарегистрирован + суточный beat (v2)."""
    from tasks.celery_app import app, prune_gateway_requests  # noqa: F401

    assert "tasks.celery_app.prune_gateway_requests" in app.tasks
    assert "prune-gateway-requests-daily" in app.conf.beat_schedule
    entry = app.conf.beat_schedule["prune-gateway-requests-daily"]
    assert entry["task"] == "tasks.celery_app.prune_gateway_requests"


def test_log_redaction_reinstalled_in_forked_worker_children():
    """Инцидент 2026-09-02: ключ сообщества в celery-worker.log из ForkPoolWorker.

    Фабрика LogRecord с маскированием обязана ставиться в worker_process_init —
    сигнале, который срабатывает уже внутри дочернего процесса пула.
    """
    from celery import signals

    import tasks.celery_app as mod

    receivers = [r for r in signals.worker_process_init.receivers]
    names = []
    for _key, ref in receivers:
        fn = ref() if callable(ref) and not hasattr(ref, "__name__") else ref
        names.append(getattr(fn, "__name__", str(fn)))
    assert "_setka_redact_logs_in_child" in names, names
    assert callable(mod._setka_redact_logs_in_child)
