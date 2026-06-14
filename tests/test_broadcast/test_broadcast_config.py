"""Тесты конфигурации сетевой рассылки (config/runtime.py)."""

from __future__ import annotations

from config import runtime


def test_broadcast_enabled_by_default(monkeypatch):
    monkeypatch.delenv("BROADCAST_DISABLED", raising=False)
    assert runtime.broadcast_disabled() is False


def test_broadcast_kill_switch(monkeypatch):
    for v in ("1", "true", "YES", "on"):
        monkeypatch.setenv("BROADCAST_DISABLED", v)
        assert runtime.broadcast_disabled() is True
    monkeypatch.setenv("BROADCAST_DISABLED", "0")
    assert runtime.broadcast_disabled() is False


def test_post_interval_default_and_override(monkeypatch):
    monkeypatch.delenv("BROADCAST_POST_INTERVAL_SECONDS", raising=False)
    assert runtime.get_broadcast_post_interval_seconds() == 5.0
    monkeypatch.setenv("BROADCAST_POST_INTERVAL_SECONDS", "8")
    assert runtime.get_broadcast_post_interval_seconds() == 8.0
    monkeypatch.setenv("BROADCAST_POST_INTERVAL_SECONDS", "junk")
    assert runtime.get_broadcast_post_interval_seconds() == 5.0


def test_default_repeat_interval(monkeypatch):
    monkeypatch.delenv("BROADCAST_DEFAULT_REPEAT_INTERVAL_HOURS", raising=False)
    assert runtime.get_broadcast_default_repeat_interval_hours() == 24.0
    monkeypatch.setenv("BROADCAST_DEFAULT_REPEAT_INTERVAL_HOURS", "12")
    assert runtime.get_broadcast_default_repeat_interval_hours() == 12.0
