"""Tests for /api/monitoring/digests-status — состояние дайджестов из parsing_stats.

В основном — pure-function проверки на `_classify_digest_row` (логика
fresh/stale/broken/dead). Это самая важная часть для регрессий — если
правило цвета сломается, на /monitoring и главной начнут светиться
ложные тревоги. Полный smoke endpoint'а с реальной БД проще проверять
руками на проде; здесь мокать всю агрегацию — не очень полезно.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from web.api.system_monitoring import (
    _DIGEST_STATUS_BROKEN_MIN_FAILED_RUNS,
    _DIGEST_STATUS_FRESH_HOURS,
    _DIGEST_STATUS_STALE_HOURS,
    _classify_digest_row,
)


def _now() -> datetime:
    return datetime(2026, 5, 26, 21, 0, 0)


def test_classify_no_runs_is_dead():
    assert _classify_digest_row(None, None, 0, _now()) == "dead"


def test_classify_fresh_run_and_success():
    now = _now()
    one_hour_ago = now - timedelta(hours=1)
    assert _classify_digest_row(one_hour_ago, one_hour_ago, 0, now) == "fresh"


def test_classify_success_within_fresh_threshold():
    now = _now()
    boundary = now - timedelta(hours=_DIGEST_STATUS_FRESH_HOURS - 1)
    assert _classify_digest_row(boundary, boundary, 0, now) == "fresh"


def test_classify_success_between_fresh_and_stale_is_stale():
    now = _now()
    mid = now - timedelta(hours=_DIGEST_STATUS_FRESH_HOURS + 3)
    assert _classify_digest_row(mid, mid, 0, now) == "stale"


def test_classify_old_success_is_dead():
    now = _now()
    old = now - timedelta(hours=_DIGEST_STATUS_STALE_HOURS + 5)
    # last_run тоже старый — это «dead» (beat либо не запускает, либо не
    # доходит до success давно).
    assert _classify_digest_row(old, old, 0, now) == "dead"


def test_classify_recent_runs_all_failed_is_broken():
    """kirov_obl-сценарий: beat жив, но pipeline валится подряд."""
    now = _now()
    just_now = now - timedelta(minutes=20)
    assert (
        _classify_digest_row(
            last_run_at=just_now,
            last_success_at=None,
            consecutive_failed=_DIGEST_STATUS_BROKEN_MIN_FAILED_RUNS,
            now_utc=now,
        )
        == "broken"
    )


def test_classify_broken_requires_recent_run():
    """Если последний запуск >24ч назад — это уже не broken, а dead."""
    now = _now()
    long_ago = now - timedelta(hours=_DIGEST_STATUS_STALE_HOURS + 5)
    assert (
        _classify_digest_row(
            last_run_at=long_ago,
            last_success_at=None,
            consecutive_failed=10,
            now_utc=now,
        )
        == "dead"
    )


def test_classify_consecutive_failed_below_threshold_uses_success_age():
    """1-2 подряд failed — пока не broken, смотрим last_success_at."""
    now = _now()
    just_now = now - timedelta(minutes=20)
    last_success = now - timedelta(hours=2)
    assert (
        _classify_digest_row(
            last_run_at=just_now,
            last_success_at=last_success,
            consecutive_failed=_DIGEST_STATUS_BROKEN_MIN_FAILED_RUNS - 1,
            now_utc=now,
        )
        == "fresh"
    )


def test_classify_recent_run_no_success_history_dead():
    """Запуски недавно, но успехов вообще не было и consecutive_failed
    не достиг порога — считаем dead (нет success_age для fresh/stale)."""
    now = _now()
    just_now = now - timedelta(minutes=20)
    assert (
        _classify_digest_row(
            last_run_at=just_now,
            last_success_at=None,
            consecutive_failed=1,
            now_utc=now,
        )
        == "dead"
    )
