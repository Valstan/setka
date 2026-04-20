"""Tests for parsing scheduler region pre-filtering."""

import os
import sys
import types
from unittest.mock import MagicMock, patch


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

def test_run_all_regions_theme_schedules_only_eligible_regions():
    celery_stub = types.ModuleType("celery")
    def _shared_task_stub(*args, **kwargs):
        if args and callable(args[0]) and len(args) == 1 and not kwargs:
            return args[0]
        def _decorator(fn):
            return fn
        return _decorator
    celery_stub.shared_task = _shared_task_stub
    with patch.dict(sys.modules, {"celery": celery_stub}):
        from tasks import parsing_scheduler_tasks as scheduler_tasks

        delayed = []

        def _fake_delay(region_code, theme):
            task = MagicMock()
            task.id = f"task-{region_code}-{theme}"
            delayed.append((region_code, theme))
            return task

        parse_task_mock = MagicMock()
        parse_task_mock.delay = MagicMock(side_effect=_fake_delay)
        def _fake_run_coro(coro):
            coro.close()
            return ["mi", "ur"]
        with patch("tasks.parsing_scheduler_tasks.run_coro", side_effect=_fake_run_coro):
            with patch("tasks.parsing_scheduler_tasks.parse_and_publish_theme", parse_task_mock):
                result = scheduler_tasks.run_all_regions_theme("novost")

        assert delayed == [("mi", "novost"), ("ur", "novost")]
        assert result["regions"] == ["mi", "ur"]
        assert result["theme"] == "novost"
        assert result["tasks"] == ["task-mi-novost", "task-ur-novost"]
