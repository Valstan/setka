"""Тесты планировщика предложки (suggested_planner) — настоящий SQL, VK инжектируется.

Ключевые утверждения:
- дублёры по умолчанию = активные соседи региона с группой, сам регион исключён;
- один план = оригинал (kind=suggested, подпись) + строка-репост на каждый дублёр,
  Σ цен = цене заказа, клиент заведён по author_vk_id, заявка ушла из «Новых»;
- цена ниже пола × размещений — отказ до единого VK-вызова;
- провал VK на оригинале → failed, репостов нет, заявка остаётся new;
- режим queue — ноль VK-вызовов, оригинал ждёт диспетчера;
- анти-спам «1 пост клиента в сообщество в день» действует и здесь.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from database.models import AdClient, AdRequest, AdScheduledPost, Region
from modules.ad_cabinet import suggested_planner as sp
from modules.ad_cabinet.client_orders import OrderError

NOW = datetime(2026, 9, 6, 12, 0)
PUBLISH_AT = datetime(2026, 9, 7, 10, 0)
MI = -158787639
UR = -168170215
KLZ = -168172770
FAR = -555


class FakePublisher:
    def __init__(self, *, fail=False, returned_id=None):
        self.calls = []
        self.fail = fail
        self.returned_id = returned_id

    async def publish_suggested(self, group_id, post_id, *, signed=True, publish_date=None):
        self.calls.append(
            {"group_id": group_id, "post_id": post_id, "signed": signed, "publish_date": publish_date}
        )
        if self.fail:
            return {"success": False, "error": "VK API error: [15] Access denied", "vk_error_code": 15}
        return {"success": True, "post_id": self.returned_id or post_id}


async def _seed(session):
    session.add_all(
        [
            Region(id=1, code="mi", name="Малмыж", vk_group_id=158787639, is_active=True, neighbors="ur,klz,dead"),
            Region(id=2, code="ur", name="Уржум", vk_group_id=168170215, is_active=True),
            Region(id=3, code="klz", name="Кильмезь", vk_group_id=168172770, is_active=True),
            Region(id=4, code="dead", name="Мёртвый", vk_group_id=444, is_active=False),
            Region(id=5, code="far", name="Далёкий", vk_group_id=555, is_active=True),
        ]
    )
    ar = AdRequest(
        id=1,
        origin="suggested",
        community_vk_id=MI,
        community_name="МАЛМЫЖ - ИНФО",
        vk_post_id=78276,
        author_vk_id=139799739,
        signer_id=139799739,
        peer_id=139799739,
        author_name="Анна Валиева",
        region_id=1,
        text_snapshot="Пени по ЖКХ растут…",
        status="new",
    )
    session.add(ar)
    await session.flush()
    region = await session.get(Region, 1)
    return region, ar


@pytest.mark.asyncio
async def test_default_dups_are_active_neighbors_with_group(db_session):
    region, _ = await _seed(db_session)
    targets = await sp.default_dup_targets(db_session, region)
    assert sorted(t[1] for t in targets) == sorted([UR, KLZ])
    cands = await sp.all_dup_candidates(db_session, region)
    by_gid = {c["community_vk_id"]: c for c in cands}
    assert by_gid[UR]["default"] and by_gid[KLZ]["default"]
    assert by_gid[FAR]["default"] is False
    assert MI not in by_gid and -444 not in by_gid


@pytest.mark.asyncio
async def test_explicit_dups_validated(db_session):
    region, _ = await _seed(db_session)
    assert [t[1] for t in await sp.resolve_dup_targets(db_session, region, [555])] == [FAR]
    assert await sp.resolve_dup_targets(db_session, region, []) == []
    with pytest.raises(OrderError):
        await sp.resolve_dup_targets(db_session, region, [999])
    with pytest.raises(OrderError):
        await sp.resolve_dup_targets(db_session, region, [MI])


@pytest.mark.asyncio
async def test_plan_creates_original_and_reposts(db_session):
    region, ar = await _seed(db_session)
    pub = FakePublisher(returned_id=99001)
    dups = await sp.default_dup_targets(db_session, region)
    res = await sp.plan_item(
        db_session, ar, publish_at=PUBLISH_AT, price=1650, dup_targets=dups, publisher=pub, now=NOW
    )
    await db_session.commit()
    assert res["ok"] is True
    assert len(pub.calls) == 1
    call = pub.calls[0]
    assert call["post_id"] == 78276 and call["signed"] is True
    assert isinstance(call["publish_date"], int) and call["publish_date"] > 0

    rows = (await db_session.execute(select(AdScheduledPost))).scalars().all()
    originals = [r for r in rows if r.kind == "suggested"]
    reposts = [r for r in rows if r.kind == "repost"]
    assert len(originals) == 1 and len(reposts) == 2
    o = originals[0]
    assert o.status == "scheduled" and o.vk_postponed_post_id == 99001 and o.signed is True
    assert o.source_ad_request_id == ar.id and o.next_attempt_at is None
    assert {r.community_vk_id for r in reposts} == {UR, KLZ}
    assert all(r.source_post_id == o.id and r.status == "scheduled" for r in reposts)
    assert all(r.next_attempt_at == PUBLISH_AT and r.publish_date == PUBLISH_AT for r in reposts)
    assert sum(r.price for r in rows) == Decimal("1650.00")
    assert len({r.order_ref for r in rows}) == 1

    client = (await db_session.execute(select(AdClient))).scalar_one()
    assert client.author_vk_id == 139799739 and client.stage == "scheduled"
    assert all(r.client_id == client.id for r in rows)
    assert ar.status == "published" and ar.client_id == client.id


@pytest.mark.asyncio
async def test_price_below_floor_rejected_before_vk(db_session):
    region, ar = await _seed(db_session)
    pub = FakePublisher()
    dups = await sp.default_dup_targets(db_session, region)
    with pytest.raises(OrderError):
        await sp.plan_item(
            db_session, ar, publish_at=PUBLISH_AT, price=100, dup_targets=dups, publisher=pub, now=NOW
        )
    assert pub.calls == []
    assert (await db_session.execute(select(AdScheduledPost))).scalars().all() == []


@pytest.mark.asyncio
async def test_empty_price_uses_floor_per_placement(db_session):
    region, ar = await _seed(db_session)
    dups = await sp.default_dup_targets(db_session, region)
    res = await sp.plan_item(
        db_session, ar, publish_at=PUBLISH_AT, price=None, dup_targets=dups, publisher=FakePublisher(), now=NOW
    )
    assert res["price_total"] == float(sp.PLACEMENT_FLOOR_RUB * 3)


@pytest.mark.asyncio
async def test_already_published_request_is_noop(db_session):
    region, ar = await _seed(db_session)
    ar.status = "published"
    pub = FakePublisher()
    res = await sp.plan_item(
        db_session, ar, publish_at=PUBLISH_AT, price=None, dup_targets=[], publisher=pub, now=NOW
    )
    assert res.get("already") is True and pub.calls == []


@pytest.mark.asyncio
async def test_vk_failure_marks_original_failed_without_reposts(db_session):
    region, ar = await _seed(db_session)
    dups = await sp.default_dup_targets(db_session, region)
    res = await sp.plan_item(
        db_session, ar, publish_at=PUBLISH_AT, price=None, dup_targets=dups, publisher=FakePublisher(fail=True), now=NOW
    )
    assert res["ok"] is False and "15" in res["error"]
    rows = (await db_session.execute(select(AdScheduledPost))).scalars().all()
    assert len(rows) == 1 and rows[0].status == "failed" and rows[0].kind == "suggested"
    assert ar.status == "new"


@pytest.mark.asyncio
async def test_queue_mode_makes_no_vk_calls(db_session):
    region, ar = await _seed(db_session)
    pub = FakePublisher()
    dups = await sp.default_dup_targets(db_session, region)
    res = await sp.plan_item(
        db_session, ar, publish_at=PUBLISH_AT, price=None, dup_targets=dups, publisher=pub, mode=sp.MODE_QUEUE, now=NOW
    )
    assert res["ok"] and pub.calls == []
    o = res["original"]
    assert o.status == "scheduled" and o.next_attempt_at == PUBLISH_AT and o.vk_postponed_post_id == 78276


@pytest.mark.asyncio
async def test_past_date_rejected(db_session):
    region, ar = await _seed(db_session)
    with pytest.raises(OrderError):
        await sp.plan_item(
            db_session, ar, publish_at=NOW - timedelta(minutes=5), price=None, dup_targets=[], publisher=FakePublisher(), now=NOW
        )


@pytest.mark.asyncio
async def test_busy_day_in_dup_community_rejected(db_session):
    region, ar = await _seed(db_session)
    client = AdClient(author_vk_id=139799739, name="Анна")
    db_session.add(client)
    await db_session.flush()
    db_session.add(
        AdScheduledPost(
            community_vk_id=UR,
            region_id=2,
            text="уже стоит",
            publish_date=PUBLISH_AT + timedelta(hours=2),
            status="scheduled",
            client_id=client.id,
        )
    )
    await db_session.flush()
    dups = await sp.default_dup_targets(db_session, region)
    with pytest.raises(OrderError) as ei:
        await sp.plan_item(
            db_session, ar, publish_at=PUBLISH_AT, price=None, dup_targets=dups, publisher=FakePublisher(), now=NOW
        )
    assert "Уржум" in str(ei.value)


@pytest.mark.asyncio
async def test_second_plan_of_same_request_blocked_by_unique(db_session):
    """Двойной клик без FOR UPDATE: второй оригинал упирается в уникум БД."""
    region, ar = await _seed(db_session)
    dups = await sp.default_dup_targets(db_session, region)
    await sp.plan_item(
        db_session, ar, publish_at=PUBLISH_AT, price=None, dup_targets=dups, publisher=FakePublisher(), now=NOW
    )
    await db_session.commit()
    ar.status = "new"  # имитируем гонку: второй запрос прочитал 'new'
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        await sp.plan_item(
            db_session, ar, publish_at=PUBLISH_AT + timedelta(days=1), price=None, dup_targets=dups, publisher=FakePublisher(), now=NOW
        )
    await db_session.rollback()
