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
