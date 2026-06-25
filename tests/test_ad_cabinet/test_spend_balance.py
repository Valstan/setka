"""Тесты напоминания о перерасходе пакета публикаций (И2).

collect_overspent / format / run_overspend_alert — без сети: фейковая сессия,
инъектируемый send. Логика «вышло > оплачено пакетом, не на кулдауне».
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import MagicMock

from database.models import AdClient
from modules.ad_cabinet import spend_balance as sb


class _FakeSession:
    """execute() возвращает заранее заданные строки (.all()); commit — no-op."""

    def __init__(self, rows):
        self._rows = rows

    async def execute(self, stmt):
        r = MagicMock()
        r.all.return_value = self._rows
        return r

    async def commit(self):
        return None


class _FakeSessionCM:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        return False


_NOW = datetime(2026, 6, 25, 12, 0)


def _client(**kw):
    defaults = dict(id=1, author_vk_id=7, name="Иван", spend_alerted_at=None)
    defaults.update(kw)
    return AdClient(**defaults)


def test_collect_overspent_flags_overspent():
    # Куплено 3, вышло 5 → перерасход +2.
    rows = [(_client(), 3, 5)]
    out = asyncio.run(sb.collect_overspent(_FakeSession(rows), now=_NOW))
    assert len(out) == 1
    assert out[0]["client_id"] == 1
    assert out[0]["paid_units"] == 3
    assert out[0]["consumed"] == 5
    assert out[0]["over"] == 2
    assert out[0]["name"] == "Иван"


def test_collect_overspent_skips_within_package():
    rows = [(_client(), 5, 3)]  # вышло 3 ≤ куплено 5
    out = asyncio.run(sb.collect_overspent(_FakeSession(rows), now=_NOW))
    assert out == []


def test_collect_overspent_skips_exactly_at_limit():
    rows = [(_client(), 4, 4)]  # ровно исчерпан — ещё не перерасход
    out = asyncio.run(sb.collect_overspent(_FakeSession(rows), now=_NOW))
    assert out == []


def test_collect_overspent_dedup_recent_alert():
    # Уже тревожили час назад (кулдаун 3 дня) → молчим.
    recent = datetime(2026, 6, 25, 11, 0)
    rows = [(_client(spend_alerted_at=recent), 3, 5)]
    out = asyncio.run(sb.collect_overspent(_FakeSession(rows), now=_NOW, cooldown_days=3))
    assert out == []


def test_collect_overspent_dedup_expired_realerts():
    # Тревожили давно (10 дней назад) → можно напомнить снова.
    old = datetime(2026, 6, 15, 12, 0)
    rows = [(_client(spend_alerted_at=old), 3, 5)]
    out = asyncio.run(sb.collect_overspent(_FakeSession(rows), now=_NOW, cooldown_days=3))
    assert len(out) == 1


def test_collect_overspent_name_fallback():
    rows = [(_client(name=None, author_vk_id=42), 1, 3)]
    out = asyncio.run(sb.collect_overspent(_FakeSession(rows), now=_NOW))
    assert out[0]["name"] == "vk42"


def test_format_overspent_alert_mentions_client_and_counts():
    items = [{"client_id": 1, "name": "Иван", "paid_units": 3, "consumed": 5, "over": 2}]
    text = sb.format_overspent_alert(items, url="https://x/ad#crm")
    assert "Иван" in text
    assert "оплачено 3" in text
    assert "вышло 5" in text
    assert "https://x/ad#crm" in text


def test_run_overspend_alert_sends_when_overspent():
    rows = [(_client(), 3, 5)]
    sent = []
    out = asyncio.run(
        sb.run_overspend_alert(
            session_factory=lambda: _FakeSessionCM(_FakeSession(rows)),
            send=lambda text: sent.append(text),
            now=_NOW,
        )
    )
    assert out == {"overspent": 1, "alerted": True}
    assert sent and "Иван" in sent[0]


def test_run_overspend_alert_noop_when_none():
    sent = []
    out = asyncio.run(
        sb.run_overspend_alert(
            session_factory=lambda: _FakeSessionCM(_FakeSession([])),
            send=lambda text: sent.append(text),
            now=_NOW,
        )
    )
    assert out == {"overspent": 0, "alerted": False}
    assert sent == []
