"""Tests for /api/monitoring/heartbeat — классификатор свежести heartbeat (#018).

По образцу test_digests_status.py: проверяем pure-функцию `_classify_heartbeat_age`
(unknown/fresh/stale), а не весь endpoint с Redis. Если порог/логика цвета
сломаются — на /monitoring начнут светиться ложные тревоги по дайджестам.
"""

from __future__ import annotations

from web.api.system_monitoring import _HEARTBEAT_FRESH_HOURS, _classify_heartbeat_age


def test_none_age_is_unknown():
    # Нет heartbeat в Redis → не пугаем красным (как watchdog #018 на None не алёртит)
    assert _classify_heartbeat_age(None) == "unknown"


def test_recent_age_is_fresh():
    assert _classify_heartbeat_age(60) == "fresh"  # минуту назад


def test_negative_age_clamped_to_fresh():
    # Рассинхрон часов даёт отрицательный возраст — считаем свежим, не падаем
    assert _classify_heartbeat_age(-100) == "fresh"


def test_just_under_threshold_is_fresh():
    assert _classify_heartbeat_age((_HEARTBEAT_FRESH_HOURS - 0.5) * 3600) == "fresh"


def test_just_over_threshold_is_stale():
    assert _classify_heartbeat_age((_HEARTBEAT_FRESH_HOURS + 0.5) * 3600) == "stale"


def test_watchdog_strict_threshold():
    """novost-watchdog использует строгий 6ч-порог — 7ч назад уже stale."""
    assert _classify_heartbeat_age(7 * 3600, fresh_hours=6) == "stale"
    assert _classify_heartbeat_age(5 * 3600, fresh_hours=6) == "fresh"
