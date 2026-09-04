"""modules.telegram_http — повторы только на сетевой отказ (G307).

Держим: ConnectTimeout/ConnectionError повторяются до ATTEMPTS с коротким таймаутом
на попытку; ReadTimeout и любой HTTP-ответ (включая 5xx/429) — НЕ повторяются;
после всех попыток исключение поднимается наружу, а не глотается.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import requests

from modules import telegram_http as th


def _resp(status=200):
    return SimpleNamespace(status_code=status, ok=status == 200, text="", json=lambda: {"ok": True})


def test_retries_connect_timeout_then_succeeds(monkeypatch):
    calls = []

    def fake_post(url, **kw):
        calls.append(kw["timeout"])
        if len(calls) < 3:
            raise requests.exceptions.ConnectTimeout("syn lost")
        return _resp()

    monkeypatch.setattr(requests, "post", fake_post)
    r = th.post("https://api.telegram.org/botX/sendMessage", json={}, sleep=None)
    assert r.status_code == 200
    assert calls == [th.ATTEMPT_TIMEOUT] * 3


def test_gives_up_after_attempts_and_raises(monkeypatch):
    n = {"c": 0}

    def fake_post(url, **kw):
        n["c"] += 1
        raise requests.exceptions.ConnectionError("down")

    monkeypatch.setattr(requests, "post", fake_post)
    with pytest.raises(requests.exceptions.ConnectionError):
        th.post("https://api.telegram.org/botX/sendMessage", json={}, sleep=None)
    assert n["c"] == th.ATTEMPTS


def test_http_error_response_is_not_retried(monkeypatch):
    n = {"c": 0}

    def fake_post(url, **kw):
        n["c"] += 1
        return _resp(429)

    monkeypatch.setattr(requests, "post", fake_post)
    r = th.post("https://api.telegram.org/botX/sendMessage", json={}, sleep=None)
    assert r.status_code == 429 and n["c"] == 1


def test_read_timeout_is_not_retried(monkeypatch):
    """Соединение было — сообщение могло уйти; повтор задвоил бы его."""
    n = {"c": 0}

    def fake_post(url, **kw):
        n["c"] += 1
        raise requests.exceptions.ReadTimeout("slow")

    monkeypatch.setattr(requests, "post", fake_post)
    with pytest.raises(requests.exceptions.ReadTimeout):
        th.post("https://api.telegram.org/botX/sendMessage", json={}, sleep=None)
    assert n["c"] == 1


def test_get_retries_too_and_sleeps_between(monkeypatch):
    slept = []
    calls = {"c": 0}

    def fake_get(url, **kw):
        calls["c"] += 1
        if calls["c"] == 1:
            raise requests.exceptions.ConnectTimeout("syn lost")
        return _resp()

    monkeypatch.setattr(requests, "get", fake_get)
    r = th.get("https://api.telegram.org/botX/getMe", sleep=slept.append)
    assert r.status_code == 200 and slept == [0.5]


def test_defaults_are_short_and_many():
    assert th.ATTEMPT_TIMEOUT <= 8 and th.ATTEMPTS >= 6
