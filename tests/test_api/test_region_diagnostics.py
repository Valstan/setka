"""Tests for /api/regions diagnostics dry-run endpoints (feat/region-diagnostics)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from web.api import regions as regions_api


class _FakeResult:
    def __init__(self, first):
        self._first = first

    def first(self):
        return self._first


class _FakeDB:
    def __init__(self, first):
        self._first = first
        self.executed = []

    async def execute(self, stmt):
        self.executed.append(stmt)
        return _FakeResult(self._first)


async def test_run_diagnostics_enqueues_dry_run_task():
    db = _FakeDB(first=(1,))  # region exists
    fake_task = MagicMock()
    fake_task.id = "diag-1"
    fake_task.state = "PENDING"
    fake_celery = MagicMock()
    fake_celery.send_task.return_value = fake_task

    with patch.dict("sys.modules", {"tasks.celery_app": MagicMock(app=fake_celery)}):
        out = await regions_api.run_region_diagnostics("mi", theme="sport", db=db)

    assert out["task_id"] == "diag-1"
    assert out["region_code"] == "mi"
    assert out["theme"] == "sport"
    fake_celery.send_task.assert_called_once()
    args, kwargs = fake_celery.send_task.call_args
    assert args[0] == "tasks.parsing_scheduler_tasks.parse_and_publish_theme"
    assert kwargs["kwargs"] == {"region_code": "mi", "theme": "sport", "dry_run": True}


async def test_run_diagnostics_missing_region_404():
    db = _FakeDB(first=None)  # region does not exist
    with pytest.raises(HTTPException) as ei:
        await regions_api.run_region_diagnostics("nope", db=db)
    assert ei.value.status_code == 404


async def test_diagnostics_status_success_returns_would_publish():
    fake_ar = MagicMock()
    fake_ar.state = "SUCCESS"
    fake_ar.ready.return_value = True
    fake_ar.result = {
        "success": True,
        "dry_run": True,
        "would_publish": [{"kind": "regular", "post_count": 3}],
    }
    with patch.dict(
        "sys.modules",
        {
            "tasks.celery_app": MagicMock(app=MagicMock()),
            "celery.result": MagicMock(AsyncResult=lambda tid, app: fake_ar),
        },
    ):
        out = await regions_api.get_diagnostics_task_status("diag-1")
    assert out["state"] == "SUCCESS"
    assert out["ready"] is True
    assert out["result"]["would_publish"][0]["post_count"] == 3
    assert out["error"] is None


async def test_diagnostics_status_failure_returns_error():
    fake_ar = MagicMock()
    fake_ar.state = "FAILURE"
    fake_ar.ready.return_value = True
    fake_ar.result = RuntimeError("kaboom")
    with patch.dict(
        "sys.modules",
        {
            "tasks.celery_app": MagicMock(app=MagicMock()),
            "celery.result": MagicMock(AsyncResult=lambda tid, app: fake_ar),
        },
    ):
        out = await regions_api.get_diagnostics_task_status("diag-2")
    assert out["state"] == "FAILURE"
    assert "kaboom" in out["error"]
    assert out["result"] is None
