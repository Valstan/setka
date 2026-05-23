"""Возраст постов для дайджеста (72 ч)."""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from modules.vk_monitor.advanced_parser import (  # noqa: E402
    DIGEST_MAX_POST_AGE_HOURS,
    _post_age_hours_utc,
)


def test_post_age_hours_fresh():
    now = time.time()
    h = _post_age_hours_utc({"date": now - 10 * 3600}, now_ts=now)
    assert abs(h - 10.0) < 0.01


def test_post_age_hours_exactly_72_accepted():
    now = time.time()
    h = _post_age_hours_utc({"date": now - 72 * 3600}, now_ts=now)
    assert h <= DIGEST_MAX_POST_AGE_HOURS


def test_post_age_hours_over_72():
    now = time.time()
    h = _post_age_hours_utc({"date": now - 73 * 3600}, now_ts=now)
    assert h > DIGEST_MAX_POST_AGE_HOURS


def test_post_age_missing_date():
    assert _post_age_hours_utc({}) is None
