"""Пинги владельцу после аудита 2026-09-05 (PR 1.6).

- заказ trusted-клиента пингует всегда, текст различает «ждёт одобрения» / «уже в отложке»;
- дедуп-ключ возвращается, если ни один канал не доставил;
- бот различает order / order_direct;
- суточные алёрты экранируют имена под parse_mode=HTML;
- отправитель алёрта возвращает True только при 200.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from modules.ad_cabinet import debtors, owner_ping
from modules.ad_cabinet.vk_bot import notify as vk_notify
from web.api.advertiser_cabinet import _pending_text


def test_pending_text_distinguishes_moderation_and_trusted():
    client = SimpleNamespace(id=5, name="Иван")
    base = {"posts": [1, 2, 3], "price_total": 1050.0}
    assert "ждёт одобрения" in _pending_text(client, {**base, "moderation": True})
    assert "долг" in _pending_text(client, {**base, "moderation": True, "debt_hold": "2500 ₽"})
    txt = _pending_text(client, {**base, "moderation": False, "debt_hold": None})
    assert "уже в VK-отложке" in txt and "3 районов" in txt and "1050 ₽" in txt


@pytest.mark.asyncio
async def test_notify_owner_releases_dedup_when_nothing_delivered(monkeypatch):
    passed, released = [], []
    monkeypatch.setattr(owner_ping, "ping_dedup_pass", lambda key, ttl: passed.append(key) or True)
    monkeypatch.setattr(owner_ping, "notify_owner", lambda text: False)
    monkeypatch.setattr(owner_ping, "release_dedup", lambda key: released.append(key))
    monkeypatch.setattr(vk_notify, "community", _none)
    out = await vk_notify.notify_owner("текст", dedup_key="chat:1")
    assert out == {"telegram": False, "vk": False}
    assert passed == ["chat:1"] and released == ["chat:1"]


async def _none():
    return None


@pytest.mark.asyncio
async def test_notify_owner_keeps_dedup_when_telegram_delivered(monkeypatch):
    released = []
    monkeypatch.setattr(owner_ping, "ping_dedup_pass", lambda key, ttl: True)
    monkeypatch.setattr(owner_ping, "notify_owner", lambda text: True)
    monkeypatch.setattr(owner_ping, "release_dedup", lambda key: released.append(key))
    monkeypatch.setattr(vk_notify, "community", _none)
    out = await vk_notify.notify_owner("текст", dedup_key="chat:1")
    assert out["telegram"] is True and released == []


def test_debtor_alert_escapes_html_names():
    text = debtors.format_debtor_alert(
        [{"name": "ООО <Рога & Копыта>", "amount": 700, "oldest_days": 5}], 3
    )
    assert "&lt;Рога &amp; Копыта&gt;" in text and "<Рога" not in text


def test_send_debtor_alert_reports_http_status(monkeypatch):
    import tasks.celery_app as ca
    from modules import telegram_http

    monkeypatch.setattr(ca, "TELEGRAM_TOKENS", {"ALERT": "t"}, raising=False)
    import config.runtime as rt

    monkeypatch.setattr(rt, "TELEGRAM_TOKENS", {"ALERT": "t"})
    monkeypatch.setattr(rt, "TELEGRAM_ALERT_CHAT_ID", "42")
    monkeypatch.setattr(
        telegram_http, "post", lambda url, json: MagicMock(ok=False, status_code=400)
    )
    assert ca._send_debtor_alert("<b>x</b>") is False
    monkeypatch.setattr(
        telegram_http, "post", lambda url, json: MagicMock(ok=True, status_code=200)
    )
    assert ca._send_debtor_alert("<b>x</b>") is True
