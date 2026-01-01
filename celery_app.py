"""
Canonical Celery entrypoint for SETKA.

We keep exactly one Celery application configuration in `tasks/celery_app.py`.
This module is a thin compatibility wrapper so code and docs can consistently use:

    celery -A celery_app worker
    celery -A celery_app beat
    from celery_app import app
"""

from tasks.celery_app import app  # noqa: F401

__all__ = ["app"]


