"""Сторожа денег (Этап 1, PR 1.3 аудита кабинета 2026-09-05) — настоящая БД.

- долговой гейт: trusted с awaiting сверх лимита/срока → заказ на одобрение, VK не трогается;
- одобрение с прошедшей датой требует новую дату, а не публикует «через 3 минуты»;
- реконсилер пингует владельца о зависшей отложке (2 ч без подтверждения VK);
- сторож pending с прошедшей датой: пометка, пинг владельцу, одно уведомление клиенту;
- фото: сборщик вложений падает, если файла нет; удалить файл активного поста нельзя.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from database.models import AdClient, AdPayment, AdScheduledPost, Region
from modules.ad_cabinet import client_orders, pending_watch
from modules.ad_cabinet import publish_reconciler as pr
from tests.test_ad_cabinet.test_client_orders import (
    MSK_NOW,
    FakePublisher,
    _factory,
    _msk_to_unix,
    _no_attachments,
    _pending_post,
    _seed_client,
    _seed_regions,
)

UTC_NOW = MSK_NOW - timedelta(hours=3)


class _CM:
    def __init__(self, session):
        self._s = session

    async def __aenter__(self):
        return self._s

    async def __aexit__(self, *exc):
        return False


async def _submit(session, client, ids, pub):
    return await client_orders.submit_order(
        session,
        client=client,
        user_id=1,
        text="реклама",
        image_paths=[],
        region_ids=ids,
        publish_at=None,
        publish_now=True,
        publisher_factory=_factory(pub),
        attachment_builder=_no_attachments,
        msk_to_unix=_msk_to_unix,
        now=MSK_NOW,
    )


# ---------------------------------------------------------------- долговой гейт


@pytest.mark.asyncio
async def test_debt_hold_reason_by_amount_and_age(db_session):
    client = await _seed_client(db_session, trusted=True)
    assert await client_orders.debt_hold_reason(db_session, client, now_utc=UTC_NOW) is None
    db_session.add(
        AdPayment(client_id=client.id, amount=500, status="awaiting", created_at=UTC_NOW)
    )
    await db_session.flush()
    assert await client_orders.debt_hold_reason(db_session, client, now_utc=UTC_NOW) is None
    db_session.add(
        AdPayment(client_id=client.id, amount=2000, status="awaiting", created_at=UTC_NOW)
    )
    await db_session.flush()
    reason = await client_orders.debt_hold_reason(db_session, client, now_utc=UTC_NOW)
    assert reason and "2500" in reason


@pytest.mark.asyncio
async def test_debt_hold_reason_by_age(db_session):
    client = await _seed_client(db_session, trusted=True)
    db_session.add(
        AdPayment(
            client_id=client.id,
            amount=100,
            status="awaiting",
            created_at=UTC_NOW - timedelta(days=10),
        )
    )
    await db_session.flush()
    reason = await client_orders.debt_hold_reason(db_session, client, now_utc=UTC_NOW)
    assert reason and "старше" in reason


@pytest.mark.asyncio
async def test_trusted_with_debt_goes_to_moderation_without_vk(db_session):
    ids = await _seed_regions(db_session, 2)
    client = await _seed_client(db_session, trusted=True)
    db_session.add(
        AdPayment(client_id=client.id, amount=5000, status="awaiting", created_at=UTC_NOW)
    )
    await db_session.flush()
    pub = FakePublisher()
    res = await _submit(db_session, client, ids, pub)
    assert res["moderation"] is True and res["debt_hold"]
    assert pub.calls == []
    assert all(p.status == "pending" for p in res["posts"])


@pytest.mark.asyncio
async def test_trusted_without_debt_publishes(db_session):
    ids = await _seed_regions(db_session, 1)
    client = await _seed_client(db_session, trusted=True)
    pub = FakePublisher()
    res = await _submit(db_session, client, ids, pub)
    assert res["moderation"] is False and res["debt_hold"] is None
    assert len(pub.calls) == 1


# ---------------------------------------------------------------- одобрение с прошедшей датой


@pytest.mark.asyncio
async def test_approve_past_date_requires_new_date(db_session):
    client = await _seed_client(db_session)
    post = await _pending_post(db_session, client, publish_date=MSK_NOW - timedelta(hours=2))
    pub = FakePublisher()
    kwargs = dict(
        publisher_factory=_factory(pub),
        attachment_builder=_no_attachments,
        msk_to_unix=_msk_to_unix,
        now=MSK_NOW,
    )
    with pytest.raises(client_orders.OrderError):
        await client_orders.approve_post(db_session, post, **kwargs)
    assert pub.calls == [] and post.status == "pending"
    with pytest.raises(client_orders.OrderError):
        await client_orders.approve_post(
            db_session, post, new_publish_at=MSK_NOW - timedelta(minutes=5), **kwargs
        )
    new_at = MSK_NOW + timedelta(days=1)
    await client_orders.approve_post(db_session, post, new_publish_at=new_at, **kwargs)
    assert post.status == "scheduled" and post.publish_date == new_at
    assert len(pub.calls) == 1


# ---------------------------------------------------------------- зависшая отложка


@pytest.mark.asyncio
async def test_reconciler_pings_owner_about_stalled_post(db_session):
    db_session.add(AdClient(id=1, author_vk_id=7, name="К"))
    stalled = AdScheduledPost(
        id=1,
        community_vk_id=-100,
        text="t",
        publish_date=MSK_NOW - timedelta(hours=3),
        status="scheduled",
        vk_postponed_post_id=55,
        client_id=1,
        price=350,
    )
    fresh = AdScheduledPost(
        id=2,
        community_vk_id=-101,  # другое сообщество: дневной уникум (096)
        text="t",
        publish_date=MSK_NOW - timedelta(minutes=30),
        status="scheduled",
        vk_postponed_post_id=56,
        client_id=1,
        price=350,
    )
    db_session.add_all([stalled, fresh])
    await db_session.commit()
    alerts = []
    out = await pr.run_reconcile(
        session_factory=lambda: _CM(db_session),
        is_published=lambda o, p: False,
        now=MSK_NOW,
        stall_alert=lambda text, key: alerts.append((text, key)),
    )
    assert out["stalled"] == 1 and out["reconciled"] == 0
    assert alerts and alerts[0][1] == "stalled:1"
    await db_session.refresh(stalled)
    await db_session.refresh(fresh)
    assert stalled.status == "scheduled" and "не подтвердил" in stalled.error_message
    assert fresh.error_message is None


# ---------------------------------------------------------------- pending с прошедшей датой


@pytest.mark.asyncio
async def test_pending_watch_marks_pings_and_notifies_once(db_session):
    client = await _seed_client(db_session)
    late = await _pending_post(db_session, client, publish_date=MSK_NOW - timedelta(hours=2))
    soon = await _pending_post(db_session, client, publish_date=MSK_NOW + timedelta(days=1))
    await db_session.commit()
    pings, notes = [], []

    async def notify(session, client_id, text):
        notes.append((client_id, text))

    out = await pending_watch.run_pending_watch(
        session_factory=lambda: _CM(db_session),
        now=MSK_NOW,
        owner_ping=lambda text, key: pings.append(key),
        client_notify=notify,
    )
    assert out == {"checked": 1, "marked": 1}
    assert pings == [f"pending_overdue:{late.id}"] and len(notes) == 1
    await db_session.refresh(late)
    await db_session.refresh(soon)
    assert late.error_message == pending_watch.MARK and late.status == "pending"
    assert soon.error_message is None
    # второй прогон: пинг владельцу повторяется (дедуп — в owner_ping), клиенту — нет
    await pending_watch.run_pending_watch(
        session_factory=lambda: _CM(db_session),
        now=MSK_NOW + timedelta(hours=1),
        owner_ping=lambda text, key: pings.append(key),
        client_notify=notify,
    )
    assert len(pings) == 2 and len(notes) == 1


# ---------------------------------------------------------------- фото


@pytest.mark.asyncio
async def test_photo_in_use_detects_active_post(db_session):
    from web.api.advertiser_cabinet import photo_in_use

    client = await _seed_client(db_session)
    db_session.add(
        AdScheduledPost(
            community_vk_id=-100,
            text="t",
            publish_date=MSK_NOW,
            status="pending",
            client_id=client.id,
            image_names=["a.jpg", "b.png"],
        )
    )
    db_session.add(
        AdScheduledPost(
            community_vk_id=-101,
            text="t",
            publish_date=MSK_NOW,
            status="published",
            client_id=client.id,
            image_names=["old.jpg"],
        )
    )
    await db_session.flush()
    assert await photo_in_use(db_session, client.id, "b.png") is not None
    assert await photo_in_use(db_session, client.id, "old.jpg") is None  # уже вышел
    assert await photo_in_use(db_session, client.id, "nope.jpg") is None


def test_attachment_builder_fails_loudly_when_file_missing(monkeypatch):
    from web.api import advertiser_cabinet as api

    monkeypatch.setattr(api, "_client_photo_paths", lambda cid: [])
    build = api._real_attachment_builder(1, "user-token")
    with pytest.raises(api.AttachmentError):
        build(-100, ["gone.jpg"])
    assert build(-100, []) == []  # без фото — без вложений, это не ошибка
    with pytest.raises(api.AttachmentError):
        api._real_attachment_builder(1, None)(-100, ["x.jpg"])


@pytest.mark.asyncio
async def test_send_one_marks_failed_when_photos_missing(db_session):
    """Пост с пропавшими фото не уходит текстом: failed с причиной, слот в пакет."""
    from web.api import advertiser_cabinet as api

    ids = await _seed_regions(db_session, 1)
    client = await _seed_client(db_session, trusted=True)
    pub = FakePublisher()

    def builder(gid, names):
        raise api.AttachmentError("фото не найдены на диске: x.jpg")

    res = await client_orders.submit_order(
        db_session,
        client=client,
        user_id=1,
        text="с фото",
        image_paths=["x.jpg"],
        region_ids=ids,
        publish_at=None,
        publish_now=True,
        publisher_factory=_factory(pub),
        attachment_builder=builder,
        msk_to_unix=_msk_to_unix,
        now=MSK_NOW,
    )
    post = res["posts"][0]
    assert post.status == "failed" and "фото не найдены" in post.error_message
    assert pub.calls == []
    assert (await db_session.execute(select(Region))).scalars().all()  # sanity: фикстуры на месте


@pytest.mark.asyncio
async def test_submit_order_accepts_photo_only_post(db_session):
    """Пост из одних фото (бот, «✅ Готово» без текста) проходит; совсем пустой — нет."""
    ids = await _seed_regions(db_session, 1)
    client = await _seed_client(db_session, trusted=True)
    pub = FakePublisher()

    def builder(gid, names):
        return ["photo-1_1"]

    common = dict(
        client=client,
        user_id=1,
        region_ids=ids,
        publish_at=None,
        publish_now=True,
        publisher_factory=_factory(pub),
        attachment_builder=builder,
        msk_to_unix=_msk_to_unix,
        now=MSK_NOW,
    )
    with pytest.raises(client_orders.OrderError):
        await client_orders.submit_order(db_session, text="", image_paths=[], **common)
    res = await client_orders.submit_order(db_session, text="", image_paths=["a.jpg"], **common)
    post = res["posts"][0]
    assert post.status == "scheduled" and post.text == "" and post.image_names == ["a.jpg"]
    assert post.attachments == "photo-1_1"
