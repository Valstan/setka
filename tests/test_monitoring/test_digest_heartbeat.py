"""Тесты Redis-heartbeat дайджестов + watchdog-алёрт (modules/digest_heartbeat).

Redis и Telegram замоканы: ``_redis_client`` подменяется in-memory фейком,
``requests.post`` — заглушкой. Сети нет.
"""

from __future__ import annotations

import pytest

from modules import digest_heartbeat as dh


class _FakeRedis:
    """Минимальный in-memory Redis: setex/get с decode_responses-семантикой."""

    def __init__(self):
        self.store = {}

    def setex(self, key, ttl, value):  # noqa: ARG002 — ttl не моделируем
        self.store[key] = str(value)

    def get(self, key):
        return self.store.get(key)

    def keys(self, pattern):
        """Мини-glob: поддерживаем только хвостовую `*` (как в all_heartbeats)."""
        if pattern.endswith("*"):
            prefix = pattern[:-1]
            return [k for k in self.store if k.startswith(prefix)]
        return [k for k in self.store if k == pattern]


@pytest.fixture(autouse=True)
def _fake_redis(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(dh, "_redis_client", fake)
    return fake


# --------------------------------------------------------------------------- #
# mark_published / last_published_ts
# --------------------------------------------------------------------------- #


def test_mark_and_read_roundtrip():
    dh.mark_published("novost", ts=1000.0)
    assert dh.last_published_ts("novost") == 1000
    # ключ — ровно тот, что документирует /celery
    assert "setka:digest_last_published:novost" in dh._redis_client.store


def test_last_published_none_when_absent():
    assert dh.last_published_ts("novost") is None


def test_mark_published_empty_topic_noop():
    dh.mark_published("")
    assert dh._redis_client.store == {}


# --------------------------------------------------------------------------- #
# all_heartbeats — скан всех тем для дашборда
# --------------------------------------------------------------------------- #


def test_all_heartbeats_empty_when_no_keys():
    assert dh.all_heartbeats() == {}


def test_all_heartbeats_returns_all_topics():
    dh.mark_published("novost", ts=1000.0)
    dh.mark_published("sport", ts=2000.0)
    dh.mark_published("kultura", ts=3000.0)
    assert dh.all_heartbeats() == {"novost": 1000, "sport": 2000, "kultura": 3000}


def test_all_heartbeats_excludes_cooldown_keys(monkeypatch):
    """Служебный cooldown-ключ не должен попадать в темы дашборда."""
    sent = {}

    class _Resp:
        status_code = 200
        text = "ok"

    monkeypatch.setattr("requests.post", lambda url, **kw: sent.setdefault("n", 0) or _Resp())

    dh.mark_published("novost", ts=0.0)
    # Простой → выставит cooldown-ключ setka:digest_last_published:stale_alert_cooldown:novost
    dh.maybe_alert_stale_digest(
        topic="novost", max_age_hours=6, telegram_token="t", chat_id="c", now=10 * 3600
    )
    hb = dh.all_heartbeats()
    assert hb == {"novost": 0}
    # cooldown-ключ присутствует в Redis, но отфильтрован
    assert any("stale_alert_cooldown" in k for k in dh._redis_client.store)


def test_all_heartbeats_skips_non_int_values():
    dh.mark_published("novost", ts=1000.0)
    dh._redis_client.store["setka:digest_last_published:broken"] = "not-a-number"
    assert dh.all_heartbeats() == {"novost": 1000}


# --------------------------------------------------------------------------- #
# maybe_alert_stale_digest
# --------------------------------------------------------------------------- #


def test_none_heartbeat_does_not_alert():
    assert (
        dh.maybe_alert_stale_digest(topic="novost", telegram_token="t", chat_id="c", now=10_000.0)
        == "unknown:no-heartbeat"
    )


def test_fresh_heartbeat_not_alerted():
    dh.mark_published("novost", ts=10_000.0)
    status = dh.maybe_alert_stale_digest(
        topic="novost",
        max_age_hours=6,
        telegram_token="t",
        chat_id="c",
        now=10_000.0 + 3600,  # 1ч назад < 6ч
    )
    assert status == "fresh"


def test_stale_without_telegram_config_skipped():
    dh.mark_published("novost", ts=0.0)
    status = dh.maybe_alert_stale_digest(
        topic="novost", max_age_hours=6, telegram_token=None, chat_id=None, now=10 * 3600
    )
    assert status == "skipped:no-telegram-config"


def test_stale_sends_alert_and_sets_cooldown(monkeypatch):
    sent = {}

    class _Resp:
        status_code = 200
        text = "ok"

    def _fake_post(url, **kwargs):
        sent["url"] = url
        sent["json"] = kwargs.get("json")
        return _Resp()

    import requests

    monkeypatch.setattr(requests, "post", _fake_post)

    dh.mark_published("novost", ts=0.0)
    status = dh.maybe_alert_stale_digest(
        topic="novost",
        max_age_hours=6,
        telegram_token="bottoken",
        chat_id="42",
        now=10 * 3600,  # 10ч простоя > 6ч
    )
    assert status == "alert-sent"
    assert "bottoken" in sent["url"]
    assert sent["json"]["chat_id"] == "42"
    # cooldown выставлен
    assert "setka:digest_last_published:stale_alert_cooldown:novost" in dh._redis_client.store


def test_cooldown_suppresses_second_alert(monkeypatch):
    calls = {"n": 0}

    class _Resp:
        status_code = 200
        text = "ok"

    def _fake_post(url, **kwargs):  # noqa: ARG001
        calls["n"] += 1
        return _Resp()

    import requests

    monkeypatch.setattr(requests, "post", _fake_post)

    dh.mark_published("novost", ts=0.0)
    first = dh.maybe_alert_stale_digest(
        topic="novost", max_age_hours=6, telegram_token="t", chat_id="c", now=10 * 3600
    )
    second = dh.maybe_alert_stale_digest(
        topic="novost", max_age_hours=6, telegram_token="t", chat_id="c", now=10 * 3600
    )
    assert first == "alert-sent"
    assert second == "skipped:cooldown"
    assert calls["n"] == 1  # второй раз Telegram НЕ дёргали


# --------------------------------------------------------------------------- #
# Интеграция: track_digest_published пишет heartbeat
# --------------------------------------------------------------------------- #


def test_track_digest_published_writes_heartbeat(monkeypatch):
    marked = {}
    monkeypatch.setattr(dh, "mark_published", lambda topic, **kw: marked.setdefault("topic", topic))

    from monitoring import metrics

    metrics.track_digest_published(region="tuzha", topic="novost", result="success")
    assert marked.get("topic") == "novost"


def test_track_digest_published_failed_no_heartbeat(monkeypatch):
    marked = {}
    monkeypatch.setattr(dh, "mark_published", lambda topic, **kw: marked.setdefault("topic", topic))

    from monitoring import metrics

    metrics.track_digest_published(region="tuzha", topic="novost", result="failed")
    assert "topic" not in marked
