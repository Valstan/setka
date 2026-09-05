"""Liveness демона ВК-бота (modules/ad_cabinet/vk_bot/heartbeat) — Этап 5, PR-2.

Redis и Telegram — двойники. Что охраняется: touch пишет unix-ts с TTL и никогда
не бросает (WARNING не чаще раза в 5 минут); сторож молчит при выключенном боте,
без Redis и без ключа, алёртит только на протухший существующий heartbeat, с
cooldown; задача и beat-запись зарегистрированы.
"""

from __future__ import annotations

import logging
import os

import pytest

from modules.ad_cabinet.vk_bot import heartbeat as hb


class _FakeRedis:
    def __init__(self):
        self.store = {}
        self.ttls = {}

    def setex(self, key, ttl, value):
        self.store[key] = str(value)
        self.ttls[key] = ttl

    def get(self, key):
        return self.store.get(key)


class _Boom:
    def setex(self, *a):
        raise RuntimeError("redis down")

    def get(self, *a):
        raise RuntimeError("redis down")


@pytest.fixture
def fake(monkeypatch):
    r = _FakeRedis()
    monkeypatch.setattr(hb, "_redis", lambda: r)
    return r


async def _on():
    return (241, "T")


async def _off():
    return None


def test_touch_writes_unix_ts_with_ttl(fake):
    assert hb.touch(fake, ts=1_800_000_000) is True
    assert fake.store[hb.HEARTBEAT_KEY] == "1800000000"
    assert fake.ttls[hb.HEARTBEAT_KEY] == 14 * 24 * 3600
    assert hb.last_ts(fake) == 1_800_000_000
    assert hb.touch() is True  # клиент по умолчанию — из _redis()


def test_touch_never_raises_and_throttles_warnings(monkeypatch, caplog):
    monkeypatch.setattr(hb, "_redis", lambda: None)
    monkeypatch.setattr(hb, "_last_warn_at", 0.0)
    with caplog.at_level(logging.WARNING, logger=hb.logger.name):
        assert hb.touch() is False
        assert hb.touch(_Boom()) is False
    assert sum("heartbeat" in r.getMessage() for r in caplog.records) == 1
    assert hb.last_ts(_Boom()) is None


@pytest.mark.asyncio
async def test_watchdog_statuses(fake, monkeypatch):
    now = 1_800_000_000.0
    kw = dict(telegram_token="tok", chat_id="1", now=now)
    assert await hb.maybe_alert_stale_vk_bot(community=_off, **kw) == "skipped:bot-off"
    assert await hb.maybe_alert_stale_vk_bot(community=_on, **kw) == "unknown:no-heartbeat"
    hb.touch(fake, ts=now - 60)
    assert await hb.maybe_alert_stale_vk_bot(community=_on, **kw) == "fresh"
    hb.touch(fake, ts=now - 20 * 60)
    st = await hb.maybe_alert_stale_vk_bot(community=_on, telegram_token=None, chat_id="1", now=now)
    assert st == "skipped:no-telegram-config"

    posted = []

    class _Resp:
        def __init__(self, code):
            self.status_code = code
            self.text = ""

    from modules import telegram_http

    monkeypatch.setattr(
        telegram_http, "post", lambda url, json=None, **k: posted.append(json) or _Resp(200)
    )
    assert await hb.maybe_alert_stale_vk_bot(community=_on, **kw) == "alert-sent"
    assert "setka-vk-bot" in posted[0]["text"] and "20 мин назад" in posted[0]["text"]
    assert fake.store.get(hb.COOLDOWN_KEY) == "1"
    assert await hb.maybe_alert_stale_vk_bot(community=_on, **kw) == "skipped:cooldown"
    assert len(posted) == 1
    # 500 от Telegram — без cooldown, чтобы повторить на следующем тике
    fake.store.pop(hb.COOLDOWN_KEY)
    monkeypatch.setattr(telegram_http, "post", lambda url, json=None, **k: _Resp(500))
    assert await hb.maybe_alert_stale_vk_bot(community=_on, **kw) == "error:http-500"
    assert hb.COOLDOWN_KEY not in fake.store


@pytest.mark.asyncio
async def test_watchdog_without_redis(monkeypatch):
    monkeypatch.setattr(hb, "_redis", lambda: None)
    st = await hb.maybe_alert_stale_vk_bot(community=_on, telegram_token="t", chat_id="1")
    assert st == "skipped:no-redis"


def test_watchdog_registered():
    import tasks.vk_bot_tasks  # noqa: F401
    from tasks.celery_app import app

    assert "tasks.vk_bot_tasks.check_vk_bot_heartbeat" in app.tasks
    entry = app.conf.beat_schedule["vk-bot-watchdog"]
    assert entry["task"] == "tasks.vk_bot_tasks.check_vk_bot_heartbeat"
    assert entry["options"]["expires"] < 600 and entry["options"]["catchup"] is False


def test_daemon_imports_heartbeat():
    """Демон импортирует модуль лениво — гвоздь, что имя и функция на месте."""
    from modules.ad_cabinet.vk_bot.heartbeat import touch

    assert callable(touch) and os.path.exists("scripts/vk_bot_daemon.py")
