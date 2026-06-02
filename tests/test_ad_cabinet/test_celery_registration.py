"""Задача scan_suggested_ads зарегистрирована и есть beat-расписание."""

from __future__ import annotations


def test_scan_task_registered():
    from tasks.celery_app import app, scan_suggested_ads  # noqa: F401

    assert "tasks.celery_app.scan_suggested_ads" in app.tasks
    assert "scan-suggested-ads" in app.conf.beat_schedule
    entry = app.conf.beat_schedule["scan-suggested-ads"]
    assert entry["task"] == "tasks.celery_app.scan_suggested_ads"
