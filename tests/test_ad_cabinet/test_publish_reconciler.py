"""Тесты авто-фиксации публикаций отложки (PR-6, run_reconcile).

VK-проверка инжектируется (is_published), сессия БД — фейковая (async CM),
чтобы покрыть чистую логику реконсиляции без сети и реальной БД.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from database.models import AdScheduledPost
from modules.ad_cabinet import publish_reconciler as pr


class _FakeSession:
    def __init__(self, rows, client=None):
        self._rows = rows
        self._client = client
        self.add = MagicMock()
        self.commit = AsyncMock()
        self.flush = AsyncMock()

    async def execute(self, stmt):
        r = MagicMock()
        r.scalars.return_value.all.return_value = self._rows
        return r

    async def get(self, model, pk):
        return self._client


class _FakeSessionCM:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        return False


def _post(**kw):
    defaults = dict(
        id=1,
        community_vk_id=-100,
        region_id=7,
        vk_postponed_post_id=55,
        publish_date=datetime(2026, 6, 1, 10, 0),
        status="scheduled",
        client_id=3,
        price=2000,
    )
    defaults.update(kw)
    return AdScheduledPost(**defaults)


def _run(rows, is_published, client=None):
    session = _FakeSession(rows, client=client)
    out = asyncio.run(
        pr.run_reconcile(
            session_factory=lambda: _FakeSessionCM(session),
            is_published=is_published,
            now=datetime(2026, 6, 2, 12, 0),
        )
    )
    return out, session


def test_published_post_is_reconciled():
    client = SimpleNamespace(id=3, stage="scheduled")
    post = _post()
    out, session = _run([post], is_published=lambda owner, pid: True, client=client)
    assert out["reconciled"] == 1
    assert post.status == "published"
    # клиент продвинут в published
    assert client.stage == "published"
    # созданы AdPublication + AdPayment(awaiting) + событие → ≥3 add
    assert session.add.call_count >= 3
    session.commit.assert_awaited()


def test_not_yet_published_skipped():
    post = _post()
    out, session = _run([post], is_published=lambda owner, pid: False)
    assert out["reconciled"] == 0
    assert post.status == "scheduled"
    session.add.assert_not_called()


def test_unknown_state_skipped():
    post = _post()
    out, _ = _run([post], is_published=lambda owner, pid: None)
    assert out["reconciled"] == 0
    assert post.status == "scheduled"


def test_no_price_no_awaiting_payment():
    """Без цены awaiting-оплата не создаётся, но публикация и статус — да."""
    client = SimpleNamespace(id=3, stage="scheduled")
    post = _post(price=None)
    out, session = _run([post], is_published=lambda owner, pid: True, client=client)
    assert out["reconciled"] == 1
    assert post.status == "published"
    # AdPublication + событие, но без AdPayment
    from database.models import AdPayment

    added = [c.args[0] for c in session.add.call_args_list if c.args]
    assert not any(isinstance(a, AdPayment) for a in added)


def test_idempotent_only_scheduled_selected():
    """Реконсиляция не трогает уже опубликованные (их нет в выборке)."""
    # Пустая выборка (как после первого прогона) → ничего не делаем.
    out, session = _run([], is_published=lambda owner, pid: True)
    assert out["reconciled"] == 0
    assert out["checked"] == 0
    session.add.assert_not_called()


def test_checker_exception_is_safe():
    def boom(owner, pid):
        raise RuntimeError("vk down")

    post = _post()
    out, _ = _run([post], is_published=boom)
    assert out["reconciled"] == 0
    assert post.status == "scheduled"
