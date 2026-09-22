"""Возраст постов для сводки — окно кандидатов (сутки с 2026-09-22)."""

import time

from modules.vk_monitor.advanced_parser import BULLETIN_MAX_POST_AGE_HOURS, _post_age_hours_utc


def test_default_window_is_one_day():
    """Решение владельца 2026-09-22: всё старше суток в ленту не берём.

    Число проверяется отдельно от арифметики ниже: та работает при любом окне,
    а владелец назвал именно сутки, и вернуть 72 можно одним символом.
    """
    assert BULLETIN_MAX_POST_AGE_HOURS == 24


def test_post_age_hours_fresh():
    now = time.time()
    h = _post_age_hours_utc({"date": now - 10 * 3600}, now_ts=now)
    assert abs(h - 10.0) < 0.01


def test_post_age_at_the_window_edge_is_accepted():
    now = time.time()
    h = _post_age_hours_utc({"date": now - BULLETIN_MAX_POST_AGE_HOURS * 3600}, now_ts=now)
    assert h <= BULLETIN_MAX_POST_AGE_HOURS


def test_post_age_past_the_window_is_rejected():
    now = time.time()
    h = _post_age_hours_utc({"date": now - (BULLETIN_MAX_POST_AGE_HOURS + 1) * 3600}, now_ts=now)
    assert h > BULLETIN_MAX_POST_AGE_HOURS


def test_post_age_missing_date():
    assert _post_age_hours_utc({}) is None
