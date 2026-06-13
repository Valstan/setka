"""Тесты трекинга должников рекламного кабинета (С4).

Логика свода (collect_debtors) и оркестрация алёрта (run_debtor_alert)
покрываются без сети: фейковая async-сессия, инъектируемый send.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import MagicMock

from database.models import AdClient, AdPayment
from modules.ad_cabinet import debtors as dbt


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows

    async def execute(self, stmt):
        r = MagicMock()
        r.all.return_value = self._rows
        return r


class _FakeSessionCM:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        return False


_NOW = datetime(2026, 6, 10, 12, 0)


def _pay(**kw):
    defaults = dict(
        id=1, client_id=1, amount=1000, status="awaiting", created_at=datetime(2026, 6, 1)
    )
    defaults.update(kw)
    return AdPayment(**defaults)


def test_collect_debtors_groups_and_sums():
    c1 = AdClient(id=1, author_vk_id=7, name="Иван")
    rows = [
        (_pay(id=1, amount=1000, created_at=datetime(2026, 6, 1)), c1),  # 9 дн.
        (_pay(id=2, amount=500, created_at=datetime(2026, 6, 5)), c1),  # 5 дн.
    ]
    out = asyncio.run(dbt.collect_debtors(_FakeSession(rows), threshold_days=3, now=_NOW))
    assert len(out) == 1
    assert out[0]["client_id"] == 1
    assert out[0]["amount"] == 1500.0
    assert out[0]["count"] == 2
    assert out[0]["oldest_days"] == 9
    assert out[0]["name"] == "Иван"


def test_collect_debtors_name_fallback_to_vk():
    c = AdClient(id=2, author_vk_id=42, name=None)
    rows = [(_pay(id=3, client_id=2), c)]
    out = asyncio.run(dbt.collect_debtors(_FakeSession(rows), threshold_days=3, now=_NOW))
    assert out[0]["name"] == "vk42"


def test_collect_debtors_empty():
    out = asyncio.run(dbt.collect_debtors(_FakeSession([]), threshold_days=3, now=_NOW))
    assert out == []


def test_format_alert_mentions_count_and_client():
    debtors = [{"client_id": 1, "name": "Иван", "amount": 1500.0, "count": 2, "oldest_days": 9}]
    text = dbt.format_debtor_alert(debtors, 3, url="https://x/ad#crm")
    assert "1500" in text
    assert "Иван" in text
    assert "https://x/ad#crm" in text


def test_run_debtor_alert_sends_when_debtors():
    c1 = AdClient(id=1, author_vk_id=7, name="Иван")
    rows = [(_pay(id=1, amount=1000, created_at=datetime(2026, 6, 1)), c1)]
    sent = []
    out = asyncio.run(
        dbt.run_debtor_alert(
            session_factory=lambda: _FakeSessionCM(_FakeSession(rows)),
            send=lambda text: sent.append(text),
            threshold_days=3,
            now=_NOW,
        )
    )
    assert out == {"debtors": 1, "alerted": True}
    assert sent and "Иван" in sent[0]


def test_run_debtor_alert_noop_when_none():
    sent = []
    out = asyncio.run(
        dbt.run_debtor_alert(
            session_factory=lambda: _FakeSessionCM(_FakeSession([])),
            send=lambda text: sent.append(text),
            threshold_days=3,
            now=_NOW,
        )
    )
    assert out == {"debtors": 0, "alerted": False}
    assert not sent
