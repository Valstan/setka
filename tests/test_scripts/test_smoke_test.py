"""Unit-тесты для ``scripts/smoke_test.py`` — post-deploy smoke (dry-run).

Скрипт — CLI-утилита вне устанавливаемого пакета, грузим напрямую через
importlib (как ``tests/test_scripts/test_check_commit_msg.py``). Сеть/Celery не
поднимаем: ``evaluate_result`` чистая, а ``run_smoke`` принимает инжектируемые
``http``/``sleep``/``now``/``log``.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

_spec = importlib.util.spec_from_file_location(
    "smoke_test", REPO_ROOT / "scripts" / "smoke_test.py"
)
smoke = importlib.util.module_from_spec(_spec)
sys.modules["smoke_test"] = smoke
_spec.loader.exec_module(smoke)


# --------------------------------------------------------------------------- #
# evaluate_result — чистая логика проверки dry_run-результата
# --------------------------------------------------------------------------- #


def _ok_result(posts: int = 3) -> Dict[str, Any]:
    return {
        "success": True,
        "dry_run": True,
        "region_code": "mi",
        "theme": "novost",
        "communities_count": 10,
        "posts_parsed": posts,
        "digests_count": 1,
    }


def test_evaluate_passes_on_success_and_enough_posts():
    assert smoke.evaluate_result(_ok_result(posts=5), min_posts=1) == []


def test_evaluate_fails_on_none_result():
    failures = smoke.evaluate_result(None, min_posts=1)
    assert len(failures) == 1
    assert "пустой результат" in failures[0]


def test_evaluate_fails_on_unsuccessful_pipeline():
    res = {"success": False, "error": "No active VK READ tokens (all in cooldown?)"}
    failures = smoke.evaluate_result(res, min_posts=1)
    assert len(failures) == 1
    assert "неуспех" in failures[0]
    assert "cooldown" in failures[0]


def test_evaluate_fails_when_posts_below_minimum():
    failures = smoke.evaluate_result(_ok_result(posts=0), min_posts=1)
    assert len(failures) == 1
    assert "спарсилось постов: 0" in failures[0]


def test_evaluate_fails_when_no_dry_run_data_and_min_positive():
    # Ранний success-возврат «нет communities для темы» — без posts_parsed.
    res = {
        "success": True,
        "message": "No 'novost' communities for region mi",
        "posts_published": 0,
    }
    failures = smoke.evaluate_result(res, min_posts=1)
    assert len(failures) == 1
    assert "не выполнен парсинг" in failures[0]


def test_evaluate_passes_with_min_zero_only_checks_success():
    res = {"success": True, "message": "No communities", "posts_published": 0}
    assert smoke.evaluate_result(res, min_posts=0) == []


# --------------------------------------------------------------------------- #
# run_smoke — оркестрация с инжектируемыми коллабораторами
# --------------------------------------------------------------------------- #


class _FakeHttp:
    """Скриптованный http: очередь ответов по (method) либо по последовательности."""

    def __init__(self, responses: List[Dict[str, Any]]):
        self._responses = list(responses)
        self.calls: List[tuple] = []

    def __call__(self, method: str, url: str, timeout: float) -> Dict[str, Any]:
        self.calls.append((method, url))
        return self._responses.pop(0)


def _run(http: _FakeHttp, **overrides) -> int:
    kwargs = dict(
        base_url="http://127.0.0.1:8000",
        region="mi",
        theme="novost",
        min_posts=1,
        timeout=30,
        poll_interval=0,
        http=http,
        sleep=lambda _s: None,
        now=_fake_clock(),
        log=lambda _m: None,
    )
    kwargs.update(overrides)
    return smoke.run_smoke(**kwargs)


def _fake_clock(step: float = 1.0):
    """Монотонные «часы»: каждый вызов +step, чтобы дедлайн в итоге наступал."""
    state = {"t": 0.0}

    def _now() -> float:
        state["t"] += step
        return state["t"]

    return _now


def test_run_smoke_happy_path_returns_zero():
    http = _FakeHttp(
        [
            {"task_id": "abc", "state": "PENDING"},  # POST trigger
            {"task_id": "abc", "state": "PROGRESS", "ready": False},  # GET poll 1
            {"task_id": "abc", "state": "SUCCESS", "ready": True, "result": _ok_result(2)},
        ]
    )
    assert _run(http) == 0
    assert http.calls[0][0] == "POST"
    assert "/api/regions/mi/diagnostics?theme=novost" in http.calls[0][1]
    assert http.calls[-1][0] == "GET"


def test_run_smoke_returns_2_when_no_task_id():
    http = _FakeHttp([{"state": "ERROR"}])  # POST без task_id
    assert _run(http) == 2


def test_run_smoke_returns_1_on_task_failure():
    http = _FakeHttp(
        [
            {"task_id": "abc"},
            {"task_id": "abc", "state": "FAILURE", "ready": True, "error": "boom"},
        ]
    )
    assert _run(http) == 1


def test_run_smoke_returns_1_on_timeout():
    # Задача никогда не ready — дедлайн наступает (clock тикает быстрее timeout).
    http = _FakeHttp([{"task_id": "abc"}] + [{"state": "PROGRESS", "ready": False}] * 50)
    assert _run(http, timeout=5, now=_fake_clock(step=2.0)) == 1


def test_run_smoke_returns_1_when_result_fails_evaluation():
    http = _FakeHttp(
        [
            {"task_id": "abc"},
            {
                "task_id": "abc",
                "state": "SUCCESS",
                "ready": True,
                "result": {"success": False, "error": "No VK group ID for region mi"},
            },
        ]
    )
    assert _run(http) == 1


def test_run_smoke_respects_min_posts_threshold():
    http = _FakeHttp(
        [
            {"task_id": "abc"},
            {"task_id": "abc", "state": "SUCCESS", "ready": True, "result": _ok_result(1)},
        ]
    )
    assert _run(http, min_posts=5) == 1


# --------------------------------------------------------------------------- #
# main — разбор аргументов
# --------------------------------------------------------------------------- #


def test_main_passes_args_through(monkeypatch):
    captured: Dict[str, Any] = {}

    def _fake_run_smoke(**kwargs):
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(smoke, "run_smoke", _fake_run_smoke)
    rc = smoke.main(["--region", "vp", "--theme", "sport", "--min-posts", "2"])
    assert rc == 0
    assert captured["region"] == "vp"
    assert captured["theme"] == "sport"
    assert captured["min_posts"] == 2
