"""Реконсилер на настоящей БД: выборка по времени в шкале МСК (аудит 2026-09-05).

Старые тесты (test_publish_reconciler.py) подменяют сессию двойником, который
игнорирует WHERE, — поэтому трёхчасовой сдвиг МСК/UTC в выборке никто не ловил.
Здесь — настоящий SQL: строка с наступившей МСК-датой берётся, будущая — нет,
«сейчас» по умолчанию — московское, а не UTC.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from database.models import AdClient, AdPayment, AdPublication, AdScheduledPost
from modules.ad_cabinet import publish_reconciler as pr

MSK = timezone(timedelta(hours=3))


class _CM:
    def __init__(self, session):
        self._s = session

    async def __aenter__(self):
        return self._s

    async def __aexit__(self, *exc):
        return False


def _row(session, **kw):
    defaults = dict(
        community_vk_id=-100,
        text="t",
        publish_date=datetime(2026, 9, 5, 20, 0),
        status="scheduled",
        vk_postponed_post_id=55,
        client_id=1,
        price=350,
    )
    defaults.update(kw)
    row = AdScheduledPost(**defaults)
    session.add(row)
    return row


def test_default_now_is_moscow_wall_clock():
    expected = datetime.now(MSK).replace(tzinfo=None)
    got = pr.now_msk()
    assert abs((got - expected).total_seconds()) < 5
    # и НЕ UTC: разница с utcnow — около трёх часов
    assert abs((got - datetime.utcnow()).total_seconds() - 3 * 3600) < 60


@pytest.mark.asyncio
async def test_due_row_in_msk_scale_is_reconciled(db_session):
    """Пост вышел в 20:00 МСК; в 20:30 МСК (=17:30 UTC) он уже должен быть зафиксирован."""
    db_session.add(AdClient(id=1, author_vk_id=7, name="К"))
    row = _row(db_session, publish_date=datetime(2026, 9, 5, 20, 0))
    await db_session.commit()
    out = await pr.run_reconcile(
        session_factory=lambda: _CM(db_session),
        is_published=lambda o, p: True,
        now=datetime(2026, 9, 5, 20, 30),
    )
    assert (out["reconciled"], out["checked"]) == (1, 1)
    await db_session.refresh(row)
    assert row.status == "published"
    pubs = (await db_session.execute(select(AdPublication))).scalars().all()
    pays = (await db_session.execute(select(AdPayment))).scalars().all()
    assert len(pubs) == 1 and pubs[0].vk_post_id == 55
    assert len(pays) == 1 and pays[0].status == "awaiting" and float(pays[0].amount) == 350.0


@pytest.mark.asyncio
async def test_future_row_is_not_touched(db_session):
    db_session.add(AdClient(id=1, author_vk_id=7, name="К"))
    row = _row(db_session, publish_date=datetime(2026, 9, 5, 21, 0))
    await db_session.commit()
    out = await pr.run_reconcile(
        session_factory=lambda: _CM(db_session),
        is_published=lambda o, p: True,
        now=datetime(2026, 9, 5, 20, 30),
    )
    assert (out["reconciled"], out["checked"]) == (0, 0)
    await db_session.refresh(row)
    assert row.status == "scheduled"


@pytest.mark.asyncio
async def test_repost_rows_are_left_to_dispatcher(db_session):
    db_session.add(AdClient(id=1, author_vk_id=7, name="К"))
    orig = _row(db_session, kind="suggested", publish_date=datetime(2026, 9, 5, 10, 0))
    await db_session.flush()
    _row(
        db_session,
        kind="repost",
        source_post_id=orig.id,
        community_vk_id=-200,
        vk_postponed_post_id=777,  # даже с id — не наша строка
        publish_date=datetime(2026, 9, 5, 10, 0),
    )
    await db_session.commit()
    out = await pr.run_reconcile(
        session_factory=lambda: _CM(db_session),
        is_published=lambda o, p: True,
        now=datetime(2026, 9, 5, 12, 0),
    )
    assert out["checked"] == 1 and out["reconciled"] == 1
    reposts = (
        (await db_session.execute(select(AdScheduledPost).where(AdScheduledPost.kind == "repost")))
        .scalars()
        .all()
    )
    assert reposts[0].status == "scheduled"
