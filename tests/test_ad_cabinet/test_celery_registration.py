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


def test_auto_greet_task_registered():
    """Авто-приветствие рекламодателю зарегистрировано + beat."""
    from tasks.celery_app import app, auto_greet_ad_requests  # noqa: F401

    assert "tasks.celery_app.auto_greet_ad_requests" in app.tasks
    assert "auto-greet-ad-requests" in app.conf.beat_schedule
    entry = app.conf.beat_schedule["auto-greet-ad-requests"]
    assert entry["task"] == "tasks.celery_app.auto_greet_ad_requests"
