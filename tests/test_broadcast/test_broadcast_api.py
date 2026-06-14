"""Юнит-тесты чистых хелперов API рассылки (clamp интервала, parse МСК)."""

from __future__ import annotations

from web.api import broadcast as b


def test_clamp_interval_single_run_passthrough():
    # repeat_count<=1 → интервал не важен, проходит как есть (неотрицательный).
    assert b._clamp_interval(0, 1) == 0.0
    assert b._clamp_interval(48, 1) == 48.0
    assert b._clamp_interval(-5, 1) == 0.0


def test_clamp_interval_repeats_floored():
    # repeat_count>1 → не меньше MIN (анти-машинганнинг при interval=0).
    assert b._clamp_interval(0, 2) == b.MIN_REPEAT_INTERVAL_HOURS
    assert b._clamp_interval(0.01, 5) == b.MIN_REPEAT_INTERVAL_HOURS
    assert b._clamp_interval(24, 3) == 24.0


def test_parse_msk_naive_kept():
    # Naive ISO трактуется как МСК wall-clock (как publish_date в ad-CRM).
    dt = b._parse_msk("2026-06-14T20:00")
    assert dt is not None and dt.tzinfo is None
    assert (dt.year, dt.month, dt.day, dt.hour, dt.minute) == (2026, 6, 14, 20, 0)


def test_parse_msk_tzaware_converted_to_msk_naive():
    # tz-aware → переводим в МСК и снимаем tz. 17:00Z = 20:00 МСК.
    dt = b._parse_msk("2026-06-14T17:00:00+00:00")
    assert dt is not None and dt.tzinfo is None and dt.hour == 20


def test_parse_msk_none():
    assert b._parse_msk(None) is None
    assert b._parse_msk("") is None
