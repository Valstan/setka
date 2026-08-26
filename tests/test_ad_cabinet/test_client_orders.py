"""Тесты заказов кабинета рекламодателя (client_orders).

VK-детали инжектируются фейками (паттерн test_publish_reconciler): считаем
ВЫЗОВЫ, а не верим статусам. Ключевые утверждения:
- не-trusted → pending и НОЛЬ VK-вызовов (модерационный гейт);
- trusted → VK-отложка на каждый район;
- price_split: Σ = прайс, копейка в копейку;
- approve идемпотентен и двигает счётчик доверия;
- ошибка VK на одной группе не роняет остальные.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from config.ad_landing import quote_price
from database.models import AdClient, AdScheduledPost, Region
from modules.ad_cabinet import client_orders

MSK_NOW = datetime(2026, 9, 1, 12, 0)


def _msk_to_unix(dt: datetime) -> int:
    return int(dt.timestamp())  # для тестов достаточно монотонности


class FakePublisher:
    """Считает вызовы; настраивается на успех/провал по группе."""

    def __init__(self, fail_groups=()):
        self.calls = []
        self.fail_groups = set(fail_groups)

    async def publish_bulletin(
        self, *, group_id, text, attachments, from_group, publish_date, signed
    ):
        self.calls.append({"group_id": group_id, "publish_date": publish_date})
        if group_id in self.fail_groups:
            return {"success": False, "error": "vk says no"}
        return {"success": True, "post_id": 1000 + len(self.calls)}


def _factory(publisher):
    async def factory(gid):
        return publisher

    return factory


def _no_attachments(gid, image_paths):
    return []


async def _seed_regions(session, n=3, *, start_id=1):
    ids = []
    for i in range(n):
        r = Region(
            id=start_id + i,
            code=f"r{start_id + i}",
            name=f"Район {start_id + i}",
            vk_group_id=100 + i,
            is_active=True,
        )
        session.add(r)
        ids.append(r.id)
    await session.flush()
    return ids


async def _seed_client(session, *, trusted=False):
    c = AdClient(name="Клиент", trusted=trusted)
    session.add(c)
    await session.flush()
    return c


# ─── price_split ─────────────────────────────────────────────────


def test_price_split_sum_invariant():
    """Σ долей == total при любом n — иначе баланс разъедется с прайсом."""
    for n in (1, 2, 3, 7, 10, 35):
        total = Decimal(quote_price(n)["price"])
        parts = client_orders.price_split(total, n)
        assert sum(parts) == total, f"n={n}"
        assert all(p >= 0 for p in parts)


def test_price_split_kopecks_go_first():
    parts = client_orders.price_split(Decimal("100.00"), 3)
    assert sum(parts) == Decimal("100.00")
    assert parts[1] == parts[2]  # остаток — только первой


# ─── resolve_targets ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resolve_targets_negative_owner_ids(db_session):
    ids = await _seed_regions(db_session, 2)
    targets = await client_orders.resolve_targets(db_session, ids)
    assert [(rid, gid < 0) for rid, gid, _name in targets] == [(ids[0], True), (ids[1], True)]


@pytest.mark.asyncio
async def test_resolve_targets_rejects_inactive_and_unknown(db_session):
    ids = await _seed_regions(db_session, 1)
    db_session.add(Region(id=99, code="off", name="Выкл", vk_group_id=999, is_active=False))
    await db_session.flush()

    with pytest.raises(client_orders.OrderError):
        await client_orders.resolve_targets(db_session, ids + [99])  # неактивный
    with pytest.raises(client_orders.OrderError):
        await client_orders.resolve_targets(db_session, [12345])  # несуществующий
    with pytest.raises(client_orders.OrderError):
        await client_orders.resolve_targets(db_session, [])  # пустой выбор


# ─── submit_order: модерационный гейт ────────────────────────────


@pytest.mark.asyncio
async def test_untrusted_order_is_pending_and_no_vk_calls(db_session):
    """Сердце модерации: у не-trusted клиента в VK не уходит НИЧЕГО."""
    ids = await _seed_regions(db_session, 3)
    client = await _seed_client(db_session, trusted=False)
    pub = FakePublisher()

    result = await client_orders.submit_order(
        db_session,
        client=client,
        user_id=1,
        text="Продам гараж",
        image_paths=[],
        region_ids=ids,
        publish_at=None,
        publish_now=True,
        publisher_factory=_factory(pub),
        attachment_builder=_no_attachments,
        msk_to_unix=_msk_to_unix,
        now=MSK_NOW,
    )

    assert result["moderation"] is True
    assert pub.calls == []  # ноль VK-вызовов
    assert [p.status for p in result["posts"]] == ["pending"] * 3
    assert all(p.order_ref == result["order_ref"] for p in result["posts"])


@pytest.mark.asyncio
async def test_trusted_order_goes_to_vk_per_region(db_session):
    ids = await _seed_regions(db_session, 3)
    client = await _seed_client(db_session, trusted=True)
    pub = FakePublisher()

    result = await client_orders.submit_order(
        db_session,
        client=client,
        user_id=1,
        text="Продам гараж",
        image_paths=[],
        region_ids=ids,
        publish_at=MSK_NOW + timedelta(hours=2),
        publish_now=False,
        publisher_factory=_factory(pub),
        attachment_builder=_no_attachments,
        msk_to_unix=_msk_to_unix,
        now=MSK_NOW,
    )

    assert result["moderation"] is False
    assert len(pub.calls) == 3  # по вызову на район
    assert [p.status for p in result["posts"]] == ["scheduled"] * 3
    assert sum(Decimal(str(p.price)) for p in result["posts"]) == Decimal(quote_price(3)["price"])


@pytest.mark.asyncio
async def test_partial_vk_failure_does_not_kill_order(db_session):
    """Отвалившаяся группа остаётся failed, остальные — scheduled."""
    ids = await _seed_regions(db_session, 3)
    client = await _seed_client(db_session, trusted=True)
    failing_gid = -abs(100)  # первая группа
    pub = FakePublisher(fail_groups={failing_gid})

    result = await client_orders.submit_order(
        db_session,
        client=client,
        user_id=1,
        text="т",
        image_paths=[],
        region_ids=ids,
        publish_at=None,
        publish_now=True,
        publisher_factory=_factory(pub),
        attachment_builder=_no_attachments,
        msk_to_unix=_msk_to_unix,
        now=MSK_NOW,
    )

    statuses = sorted(p.status for p in result["posts"])
    assert statuses == ["failed", "scheduled", "scheduled"]
    failed = [p for p in result["posts"] if p.status == "failed"][0]
    assert failed.error_message


# ─── submit_order: валидация ─────────────────────────────────────


@pytest.mark.asyncio
async def test_empty_post_rejected(db_session):
    ids = await _seed_regions(db_session, 1)
    client = await _seed_client(db_session)
    with pytest.raises(client_orders.OrderError):
        await client_orders.submit_order(
            db_session,
            client=client,
            user_id=1,
            text="   ",
            image_paths=[],
            region_ids=ids,
            publish_at=None,
            publish_now=True,
            publisher_factory=_factory(FakePublisher()),
            attachment_builder=_no_attachments,
            msk_to_unix=_msk_to_unix,
            now=MSK_NOW,
        )


def test_schedule_window():
    with pytest.raises(client_orders.OrderError):
        client_orders.validate_publish_at(
            MSK_NOW + timedelta(minutes=5), publish_now=False, now=MSK_NOW
        )
    with pytest.raises(client_orders.OrderError):
        client_orders.validate_publish_at(
            MSK_NOW + timedelta(days=90), publish_now=False, now=MSK_NOW
        )
    ok = client_orders.validate_publish_at(
        MSK_NOW + timedelta(hours=1), publish_now=False, now=MSK_NOW
    )
    assert ok == MSK_NOW + timedelta(hours=1)
    now_pub = client_orders.validate_publish_at(None, publish_now=True, now=MSK_NOW)
    assert now_pub == MSK_NOW + client_orders.PUBLISH_NOW_DELAY


@pytest.mark.asyncio
async def test_active_posts_limit(db_session):
    ids = await _seed_regions(db_session, 1)
    client = await _seed_client(db_session)
    for _ in range(client_orders.MAX_ACTIVE_POSTS):
        db_session.add(
            AdScheduledPost(
                community_vk_id=-100,
                text="x",
                publish_date=MSK_NOW,
                status="pending",
                client_id=client.id,
            )
        )
    await db_session.flush()

    with pytest.raises(client_orders.OrderError):
        await client_orders.submit_order(
            db_session,
            client=client,
            user_id=1,
            text="ещё",
            image_paths=[],
            region_ids=ids,
            publish_at=None,
            publish_now=True,
            publisher_factory=_factory(FakePublisher()),
            attachment_builder=_no_attachments,
            msk_to_unix=_msk_to_unix,
            now=MSK_NOW,
        )


# ─── approve / reject ────────────────────────────────────────────


async def _pending_post(session, client, *, publish_date=None):
    p = AdScheduledPost(
        community_vk_id=-100,
        text="на модерацию",
        publish_date=publish_date or (MSK_NOW + timedelta(hours=1)),
        status="pending",
        client_id=client.id,
        price=Decimal("350.00"),
    )
    session.add(p)
    await session.flush()
    return p


@pytest.mark.asyncio
async def test_approve_sends_to_vk_and_counts_trust(db_session):
    client = await _seed_client(db_session, trusted=False)
    pub = FakePublisher()

    for i in range(client_orders.TRUST_AFTER_POSTS):
        post = await _pending_post(db_session, client)
        await client_orders.approve_post(
            db_session,
            post,
            publisher_factory=_factory(pub),
            attachment_builder=_no_attachments,
            msk_to_unix=_msk_to_unix,
            now=MSK_NOW,
        )
        assert post.status == "scheduled"
        assert post.moderated_at is not None

    assert len(pub.calls) == client_orders.TRUST_AFTER_POSTS
    assert client.approved_posts_count == client_orders.TRUST_AFTER_POSTS
    assert client.trusted is True  # автоперевод на пороге


@pytest.mark.asyncio
async def test_approve_is_idempotent(db_session):
    """Повторный клик «Одобрить» не публикует второй раз."""
    client = await _seed_client(db_session)
    post = await _pending_post(db_session, client)
    pub = FakePublisher()

    kwargs = dict(
        publisher_factory=_factory(pub),
        attachment_builder=_no_attachments,
        msk_to_unix=_msk_to_unix,
        now=MSK_NOW,
    )
    await client_orders.approve_post(db_session, post, **kwargs)
    await client_orders.approve_post(db_session, post, **kwargs)

    assert len(pub.calls) == 1
    assert client.approved_posts_count == 1


@pytest.mark.asyncio
async def test_approve_reanchors_past_date(db_session):
    """Дата, прошедшая за время модерации, переносится в ближайшее будущее."""
    client = await _seed_client(db_session)
    post = await _pending_post(db_session, client, publish_date=MSK_NOW - timedelta(hours=2))
    await client_orders.approve_post(
        db_session,
        post,
        publisher_factory=_factory(FakePublisher()),
        attachment_builder=_no_attachments,
        msk_to_unix=_msk_to_unix,
        now=MSK_NOW,
    )
    assert post.publish_date == MSK_NOW + client_orders.PUBLISH_NOW_DELAY


@pytest.mark.asyncio
async def test_reject_no_vk_and_keeps_comment(db_session):
    client = await _seed_client(db_session)
    post = await _pending_post(db_session, client)
    await client_orders.reject_post(db_session, post, comment="не наш формat")
    assert post.status == "rejected"
    assert post.moderation_comment == "не наш формat"
    assert client.approved_posts_count == 0  # счётчик двигает только approve
