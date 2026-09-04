"""Тесты диспетчера планировщика предложки (repost_dispatcher) — настоящий SQL.

Считаем ВЫЗОВЫ VK, а не верим статусам:
- не due — не берётся; lease защищает от двойного взятия;
- оригинал не вышел → ждём (+LEASE), репоста нет; вышел → репост + фиксация обоих;
- дедлайн 2 ч → failed + алёрт; 9 → все due сдвинуты, тик прерван; 214 → failed;
- ошибка без кода → ретрай через RETRY_WAIT, после MAX_ATTEMPTS — failed;
- оригинал в режиме queue публикуется без publish_date.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from database.models import AdClient, AdPayment, AdPublication, AdScheduledPost
from modules.ad_cabinet import repost_dispatcher as rd

T0 = datetime(2026, 9, 7, 10, 0)
NOW = T0 + timedelta(minutes=1)
SRC = -100
DUP1 = -200
DUP2 = -300


class _CM:
    def __init__(self, session):
        self._s = session

    async def __aenter__(self):
        return self._s

    async def __aexit__(self, *exc):
        return False


class FakePublisher:
    def __init__(self, *, repost_errors=None, suggested_id=777):
        self.reposts = []
        self.suggested = []
        self.repost_errors = repost_errors or {}
        self.suggested_id = suggested_id
        self._n = 0

    async def publish_repost(self, *, group_id, source_owner_id, source_post_id, message=""):
        self.reposts.append((group_id, source_owner_id, source_post_id))
        err = self.repost_errors.get(group_id)
        if err:
            return {"success": False, "error": err[0], "vk_error_code": err[1]}
        self._n += 1
        return {"success": True, "post_id": 5000 + self._n}

    async def publish_suggested(self, group_id, post_id, *, signed=True, publish_date=None):
        self.suggested.append(
            {"group_id": group_id, "post_id": post_id, "publish_date": publish_date}
        )
        return {"success": True, "post_id": self.suggested_id}


class Alerts:
    def __init__(self):
        self.msgs = []

    def __call__(self, text):
        self.msgs.append(text)


async def _seed(session, *, original_status="scheduled", original_next=None, attempts=0):
    client = AdClient(id=1, author_vk_id=42, name="Клиент")
    session.add(client)
    orig = AdScheduledPost(
        id=1,
        kind="suggested",
        community_vk_id=SRC,
        text="оригинал",
        publish_date=T0,
        status=original_status,
        vk_postponed_post_id=555,
        next_attempt_at=original_next,
        client_id=1,
        price=300,
        signed=True,
    )
    session.add(orig)
    await session.flush()
    reposts = []
    for i, gid in enumerate((DUP1, DUP2), start=2):
        r = AdScheduledPost(
            id=i,
            kind="repost",
            source_post_id=orig.id,
            community_vk_id=gid,
            text="оригинал",
            publish_date=T0,
            next_attempt_at=T0,
            status="scheduled",
            client_id=1,
            price=300,
            attempts=attempts,
        )
        session.add(r)
        reposts.append(r)
    await session.commit()
    return orig, reposts


def _run(session, **kw):
    kw.setdefault("session_factory", lambda: _CM(session))
    kw.setdefault("interval", 0)
    kw.setdefault("now", NOW)
    kw.setdefault("alert", Alerts())
    return rd.run_repost_dispatch(**kw)


def _factory(pub):
    async def f(gid):
        return pub

    return f


@pytest.mark.asyncio
async def test_not_due_rows_are_not_taken(db_session):
    _, reposts = await _seed(db_session)
    for r in reposts:
        r.next_attempt_at = NOW + timedelta(minutes=10)
    await db_session.commit()
    pub = FakePublisher()
    stats = await _run(db_session, publisher_factory=_factory(pub), is_published=lambda o, p: True)
    assert stats["taken"] == 0 and pub.reposts == []


@pytest.mark.asyncio
async def test_waits_while_original_not_published(db_session):
    _, reposts = await _seed(db_session)
    pub = FakePublisher()
    stats = await _run(db_session, publisher_factory=_factory(pub), is_published=lambda o, p: False)
    assert stats["waiting"] == 2 and pub.reposts == []
    for r in reposts:
        await db_session.refresh(r)
        assert r.status == "scheduled" and r.next_attempt_at == NOW + rd.LEASE and r.attempts == 1


@pytest.mark.asyncio
async def test_original_published_then_reposts_recorded(db_session):
    orig, reposts = await _seed(db_session)
    pub = FakePublisher()
    checks = []

    def is_published(owner, pid):
        checks.append((owner, pid))
        return True

    stats = await _run(db_session, publisher_factory=_factory(pub), is_published=is_published)
    assert stats["published"] == 2
    assert checks[0] == (SRC, 555)
    assert pub.reposts == [(DUP1, SRC, 555), (DUP2, SRC, 555)]
    await db_session.refresh(orig)
    assert orig.status == "published"
    pubs = (await db_session.execute(select(AdPublication))).scalars().all()
    assert {p.scheduled_post_id for p in pubs} == {1, 2, 3}
    by_sched = {p.scheduled_post_id: p for p in pubs}
    assert by_sched[1].vk_post_id == 555 and by_sched[2].vk_post_id == 5001
    payments = (await db_session.execute(select(AdPayment))).scalars().all()
    assert len(payments) == 3 and all(p.status == "awaiting" for p in payments)
    for r in reposts:
        await db_session.refresh(r)
        assert r.status == "published" and r.vk_postponed_post_id in (5001, 5002)


@pytest.mark.asyncio
async def test_already_published_original_needs_no_check(db_session):
    await _seed(db_session, original_status="published")
    pub = FakePublisher()

    def boom(o, p):
        raise AssertionError("is_published must not be called")

    stats = await _run(db_session, publisher_factory=_factory(pub), is_published=boom)
    assert stats["published"] == 2


@pytest.mark.asyncio
async def test_deadline_fails_repost_with_alert(db_session):
    _, reposts = await _seed(db_session)
    alerts = Alerts()
    pub = FakePublisher()
    late = T0 + rd.REPOST_DEADLINE + timedelta(minutes=1)
    for r in reposts:
        r.next_attempt_at = late - timedelta(minutes=1)
    await db_session.commit()
    stats = await _run(
        db_session,
        publisher_factory=_factory(pub),
        is_published=lambda o, p: False,
        now=late,
        alert=alerts,
    )
    assert stats["failed"] == 2 and pub.reposts == []
    assert len(alerts.msgs) == 2
    for r in reposts:
        await db_session.refresh(r)
        assert r.status == "failed"


@pytest.mark.asyncio
async def test_flood_control_stops_tick_and_shifts_all_due(db_session):
    _, reposts = await _seed(db_session, original_status="published")
    alerts = Alerts()
    pub = FakePublisher(repost_errors={DUP1: ("VK API error: [9] Flood control", 9)})
    stats = await _run(db_session, publisher_factory=_factory(pub), alert=alerts)
    assert stats["stopped"] is True and stats["published"] == 0
    assert pub.reposts == [(DUP1, SRC, 555)]  # второй не пробовали
    assert alerts.msgs and "Flood" in alerts.msgs[0] or "flood" in alerts.msgs[0].lower()
    for r in reposts:
        await db_session.refresh(r)
        assert r.status == "scheduled"
        assert r.next_attempt_at >= NOW + timedelta(hours=23)


@pytest.mark.asyncio
async def test_wall_code_214_fails_only_that_row(db_session):
    _, reposts = await _seed(db_session, original_status="published")
    pub = FakePublisher(
        repost_errors={DUP1: ("VK API error: [214] Access to adding post denied", 214)}
    )
    stats = await _run(db_session, publisher_factory=_factory(pub))
    assert stats["failed"] == 1 and stats["published"] == 1
    await db_session.refresh(reposts[0])
    await db_session.refresh(reposts[1])
    assert reposts[0].status == "failed" and "214" in reposts[0].error_message
    assert reposts[1].status == "published"


@pytest.mark.asyncio
async def test_error_without_code_retries_then_fails(db_session):
    _, reposts = await _seed(db_session, original_status="published", attempts=rd.MAX_ATTEMPTS - 2)
    pub = FakePublisher(
        repost_errors={DUP1: ("connection reset", None), DUP2: ("connection reset", None)}
    )
    stats = await _run(db_session, publisher_factory=_factory(pub))
    assert stats["retry"] == 2
    for r in reposts:
        await db_session.refresh(r)
        assert r.status == "scheduled" and r.next_attempt_at == NOW + rd.RETRY_WAIT
    # следующая попытка достигает предела → failed
    for r in reposts:
        r.next_attempt_at = NOW
    await db_session.commit()
    stats = await _run(db_session, publisher_factory=_factory(pub))
    assert stats["failed"] == 2
    for r in reposts:
        await db_session.refresh(r)
        assert r.status == "failed" and r.attempts == rd.MAX_ATTEMPTS


@pytest.mark.asyncio
async def test_queue_mode_original_is_published_now(db_session):
    orig, reposts = await _seed(db_session, original_next=T0)
    pub = FakePublisher(suggested_id=9001)
    stats = await _run(db_session, publisher_factory=_factory(pub), is_published=lambda o, p: False)
    # оригинал (kind=suggested) идёт первым и публикуется без publish_date
    assert pub.suggested == [{"group_id": SRC, "post_id": 555, "publish_date": None}]
    await db_session.refresh(orig)
    assert orig.status == "published" and orig.vk_postponed_post_id == 9001
    # репосты в том же тике видят src.status == published и уходят
    assert stats["published"] == 3
    assert pub.reposts == [(DUP1, SRC, 9001), (DUP2, SRC, 9001)]


@pytest.mark.asyncio
async def test_cancelled_original_fails_reposts(db_session):
    _, reposts = await _seed(db_session, original_status="cancelled")
    pub = FakePublisher()
    stats = await _run(db_session, publisher_factory=_factory(pub))
    assert stats["failed"] == 2 and pub.reposts == []


@pytest.mark.asyncio
async def test_lease_prevents_double_take(db_session):
    await _seed(db_session)
    pub = FakePublisher()
    await _run(db_session, publisher_factory=_factory(pub), is_published=lambda o, p: False)
    stats = await _run(db_session, publisher_factory=_factory(pub), is_published=lambda o, p: True)
    assert stats["taken"] == 0  # lease ещё действует


@pytest.mark.asyncio
async def test_disabled_flag_skips(db_session, monkeypatch):
    monkeypatch.setenv("AD_REPOST_DISABLED", "1")
    await _seed(db_session, original_status="published")
    pub = FakePublisher()
    stats = await _run(db_session, publisher_factory=_factory(pub))
    assert stats.get("skipped") == "disabled" and pub.reposts == []
