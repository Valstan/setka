"""Unit-тесты для ``scripts/wait_for_health.py`` — health-поллинг после рестарта.

Скрипт — CLI вне устанавливаемого пакета, грузим через importlib (как
``test_smoke_test.py``). Сеть не поднимаем: ``poll_health`` принимает
инжектируемые ``check``/``sleep``/``now``/``log``.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import List

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

_spec = importlib.util.spec_from_file_location(
    "wait_for_health", REPO_ROOT / "scripts" / "wait_for_health.py"
)
wfh = importlib.util.module_from_spec(_spec)
sys.modules["wait_for_health"] = wfh
_spec.loader.exec_module(wfh)


class _Clock:
    """Возвращает значения из списка по одному (последнее повторяется)."""

    def __init__(self, ticks: List[float]):
        self._ticks = list(ticks)
        self._i = 0

    def __call__(self) -> float:
        v = self._ticks[min(self._i, len(self._ticks) - 1)]
        self._i += 1
        return v


def _checker(statuses: List[int]):
    seq = iter(statuses)
    last = [statuses[-1]]

    def check() -> int:
        try:
            last[0] = next(seq)
        except StopIteration:
            pass
        return last[0]

    return check


def test_immediate_success_one_attempt():
    sleeps: List[float] = []
    res = wfh.poll_health(
        _checker([200]),
        timeout=90,
        interval=3,
        now=_Clock([0, 0]),
        sleep=sleeps.append,
    )
    assert res["ok"] is True
    assert res["attempts"] == 1
    assert res["last_status"] == 200
    assert sleeps == []  # успех с первой — не спим


def test_success_after_retries():
    sleeps: List[float] = []
    res = wfh.poll_health(
        _checker([0, 0, 200]),  # сервис стартует: 000 → 000 → 200
        timeout=90,
        interval=3,
        now=_Clock([0, 1, 2, 3]),
        sleep=sleeps.append,
    )
    assert res["ok"] is True
    assert res["attempts"] == 3
    assert res["last_status"] == 200
    assert sleeps == [3, 3]  # два ретрая до успеха


def test_timeout_failure_reports_last_status():
    sleeps: List[float] = []
    logs: List[str] = []
    res = wfh.poll_health(
        _checker([0, 0, 0, 0]),  # так и не поднялся
        timeout=5,
        interval=3,
        now=_Clock([0, 1, 4, 6]),  # 6 >= deadline(5) на 3-й попытке
        sleep=sleeps.append,
        log=logs.append,
    )
    assert res["ok"] is False
    assert res["attempts"] == 3
    assert res["last_status"] == 0
    assert logs  # ретраи логировались


def test_at_least_one_attempt_when_timeout_zero():
    res = wfh.poll_health(
        _checker([503]),
        timeout=0,
        interval=3,
        now=_Clock([0, 0]),
        sleep=lambda _s: None,
    )
    assert res["ok"] is False
    assert res["attempts"] == 1
    assert res["last_status"] == 503


def test_custom_expect_code():
    res = wfh.poll_health(
        _checker([204]),
        timeout=10,
        interval=1,
        expect=204,
        now=_Clock([0, 0]),
        sleep=lambda _s: None,
    )
    assert res["ok"] is True
    assert res["attempts"] == 1


def test_main_returns_0_on_ok(monkeypatch):
    monkeypatch.setattr(wfh, "fetch_status", lambda *a, **k: 200)
    rc = wfh.main(["--timeout", "1", "--interval", "0"])
    assert rc == 0


def test_main_returns_1_on_timeout(monkeypatch):
    monkeypatch.setattr(wfh, "fetch_status", lambda *a, **k: 0)
    rc = wfh.main(["--timeout", "0", "--interval", "0"])
    assert rc == 1
