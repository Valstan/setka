"""Таски рассылки зарегистрированы + есть beat-расписание."""

from __future__ import annotations


def test_dispatch_task_registered():
    from tasks.broadcast_tasks import dispatch_broadcasts  # noqa: F401
    from tasks.celery_app import app

    assert "tasks.broadcast_tasks.dispatch_broadcasts" in app.tasks
    assert "broadcast-dispatch" in app.conf.beat_schedule
    entry = app.conf.beat_schedule["broadcast-dispatch"]
    assert entry["task"] == "tasks.broadcast_tasks.dispatch_broadcasts"


def test_watchdog_task_registered():
    from tasks.broadcast_tasks import check_broadcast_heartbeat  # noqa: F401
    from tasks.celery_app import app

    assert "tasks.broadcast_tasks.check_broadcast_heartbeat" in app.tasks
    assert "broadcast-watchdog" in app.conf.beat_schedule
    entry = app.conf.beat_schedule["broadcast-watchdog"]
    assert entry["task"] == "tasks.broadcast_tasks.check_broadcast_heartbeat"


def test_broadcast_module_included():
    from tasks.celery_app import app

    assert "tasks.broadcast_tasks" in app.conf.include
