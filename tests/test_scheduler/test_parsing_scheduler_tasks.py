"""Tests for parsing scheduler region pre-filtering."""

import sys
import types
from unittest.mock import MagicMock, patch


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


class _CapturingSession:
    """Async session double that records executed statements."""

    def __init__(self, captured):
        self._captured = captured

    async def execute(self, stmt):
        self._captured.append(stmt)
        result = MagicMock()
        result.scalars.return_value.all.return_value = []
        return result


class _CapturingSessionCM:
    def __init__(self, captured):
        self._captured = captured

    async def __aenter__(self):
        return _CapturingSession(self._captured)

    async def __aexit__(self, *exc):
        return False


def _compiled_region_gate_sql(theme, strict):
    """Run the real ``_get_regions`` query and return its compiled SQL.

    Patches ``AsyncSessionLocal`` so the coroutine executes for real (via
    ``run_coro``) but against a session double that just records the statement.
    """
    celery_stub = types.ModuleType("celery")
    celery_stub.shared_task = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
    with patch.dict(sys.modules, {"celery": celery_stub}):
        from tasks import parsing_scheduler_tasks as scheduler_tasks

        captured = []
        with patch(
            "database.connection.AsyncSessionLocal",
            lambda: _CapturingSessionCM(captured),
        ):
            with patch("tasks.parsing_scheduler_tasks.parse_and_publish_theme", MagicMock()):
                result = scheduler_tasks.run_all_regions_theme(theme, strict=strict)

    assert result["regions"] == []
    assert captured, "expected the region-selection query to be executed"
    # Default (dialect-agnostic) compiler — не дёргает psycopg2/asyncpg DBAPI;
    # оператор ``->>`` и ex()-подзапрос рендерятся как есть.
    return str(captured[0].compile(compile_kwargs={"literal_binds": True}))


def test_region_gate_admits_community_mode_oblast_without_region_config():
    """community-mode регионы (config.digest_mode='communities') должны попадать
    в тематическую волну даже без строки RegionConfig (баг kirov_obl, 2026-05).

    Без этого область, переведённая на собственный пул, молча выпадала из
    каждой волны: каскад снят, а RegionConfig-гейт её не пускал.
    """
    for strict in (True, False):
        sql = _compiled_region_gate_sql("nauka", strict=strict).lower()
        # OR-ветка community-mode присутствует
        assert "digest_mode" in sql
        assert "communities" in sql
        # старая ветка (наличие RegionConfig) сохранена
        assert "region_configs" in sql
