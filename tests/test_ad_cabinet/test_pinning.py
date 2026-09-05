"""Закреп на сутки (Этап 2, PR 2C).

- окно закрепа ±сутки на сообщество: занятое окно — отказ заказа с закрепом;
- цена: +200 ₽ за сообщество поверх скидки, пакет закреп не покрывает;
- после выхода: wall.pin через инъекцию, pinned_at/pinned_until, событие;
  неудача — pin_failed, выход всё равно зафиксирован;
- run_unpin снимает по сроку и ставит unpinned_at; задача зарегистрирована.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from database.models import (
    AdClient,
    AdClientPackage,
    AdInteraction,
    AdPublication,
    AdScheduledPost,
    Region,
)
from modules.ad_cabinet import client_orders, pinning
from modules.ad_cabinet.publish_reconciler import record_published

MSK_NOW = datetime(2026, 9, 10, 12, 0)


async def _client(session, **kw):
    c = AdClient(name=kw.pop("name", "К"), trusted=False, **kw)
    session.add(c)
    await session.flush()
    return c


async def _regions(session, n=3):
    rows = [
        Region(name=f"Р{i}", code=f"r{i}", vk_group_id=-(100 + i), is_active=True) for i in range(n)
    ]
    session.add_all(rows)
    await session.flush()
    return rows


async def _no_att(*a, **k):
    return []


def _kw(client, region_ids, *, publish_at, pinned=True):
    return dict(
        client=client,
        user_id=1,
        text="реклама",
        image_paths=[],
        region_ids=region_ids,
        publish_at=publish_at,
        publish_now=False,
        publisher_factory=lambda: None,
        attachment_builder=_no_att,
        msk_to_unix=lambda d: 0,
        now=MSK_NOW,
        pinned=pinned,
    )


@pytest.mark.asyncio
async def test_pinned_order_adds_pin_price_and_marks_rows(db_session):
    regions = await _regions(db_session, 2)
    c = await _client(db_session)
    res = await client_orders.submit_order(
        db_session, **_kw(c, [r.id for r in regions], publish_at=MSK_NOW + timedelta(days=1))
    )
    assert res["price_total"] == 700 + 400  # 2 × 350 + 2 × 200
    assert res["quote"]["pin_price"] == 400 and res["quote"]["pinned"] is True
    assert all(p.pinned for p in res["posts"])
    assert sum(float(p.price) for p in res["posts"]) == 1100.0


@pytest.mark.asyncio
async def test_pin_window_conflict_refuses_second_order(db_session):
    regions = await _regions(db_session, 2)
    c = await _client(db_session)
    other = await _client(db_session, name="Другой")
    when = MSK_NOW + timedelta(days=1)
    await client_orders.submit_order(db_session, **_kw(c, [regions[0].id], publish_at=when))
    # Другой клиент, то же сообщество, +20 ч — окно занято.
    with pytest.raises(client_orders.OrderError) as e:
        await client_orders.submit_order(
            db_session, **_kw(other, [regions[0].id], publish_at=when + timedelta(hours=20))
        )
    assert "Закреп" in str(e.value)
    # Без закрепа — можно (это обычный пост в другой день).
    ok = await client_orders.submit_order(
        db_session,
        **_kw(other, [regions[0].id], publish_at=when + timedelta(days=1, hours=1), pinned=False),
    )
    assert ok["price_total"] == 350
    # Другое сообщество или +25 ч — окно свободно.
    assert await pinning.pin_conflicts(db_session, [(0, -101)], when) == []
    assert await pinning.pin_conflicts(db_session, [(0, -100)], when + timedelta(hours=25)) == []
    assert await pinning.pin_conflicts(db_session, [(0, -100)], when - timedelta(hours=23)) == [
        -100
    ]


@pytest.mark.asyncio
async def test_package_order_still_pays_for_pin(db_session):
    regions = await _regions(db_session, 2)
    c = await _client(db_session)
    db_session.add(
        AdClientPackage(
            client_id=c.id, kind="free_promo", posts_total=3, paid_at=datetime(2026, 9, 1)
        )
    )
    await db_session.flush()
    res = await client_orders.submit_order(
        db_session, **_kw(c, [r.id for r in regions], publish_at=MSK_NOW + timedelta(days=1))
    )
    assert res["price_total"] == 400 and res["quote"]["package_id"]
    assert [float(p.price) for p in res["posts"]] == [200.0, 200.0]


@pytest.mark.asyncio
async def test_record_published_pins_and_logs(db_session):
    c = await _client(db_session)
    row = AdScheduledPost(
        community_vk_id=-100,
        text="t",
        publish_date=MSK_NOW,
        status="scheduled",
        vk_postponed_post_id=555,
        client_id=c.id,
        price=Decimal("550"),
        pinned=True,
    )
    db_session.add(row)
    await db_session.flush()
    calls = []

    async def pinner(owner_id, post_id):
        calls.append((owner_id, post_id))
        return {"success": True}

    pub = await record_published(db_session, row, pinner=pinner, notify=False)
    assert calls == [(-100, 555)]
    assert pub.pinned_at is not None and pub.pinned_until == pub.pinned_at + timedelta(hours=24)
    kinds = {i.kind for i in (await db_session.execute(select(AdInteraction))).scalars().all()}
    assert {"published", "pinned"} <= kinds


@pytest.mark.asyncio
async def test_record_published_survives_pin_failure(db_session):
    c = await _client(db_session)
    row = AdScheduledPost(
        community_vk_id=-100,
        text="t",
        publish_date=MSK_NOW,
        status="scheduled",
        vk_postponed_post_id=556,
        client_id=c.id,
        price=Decimal("550"),
        pinned=True,
    )
    db_session.add(row)
    await db_session.flush()

    async def pinner(owner_id, post_id):
        raise RuntimeError("VK 15")

    pub = await record_published(db_session, row, pinner=pinner, notify=False)
    assert row.status == "published" and pub.pinned_at is None
    kinds = [i.kind for i in (await db_session.execute(select(AdInteraction))).scalars().all()]
    assert "pin_failed" in kinds and "published" in kinds

    # Обычная строка без закрепа pinner не зовёт вовсе.
    plain = AdScheduledPost(
        community_vk_id=-101,  # другой день-слот того же клиента (096)
        text="t",
        publish_date=MSK_NOW,
        status="scheduled",
        vk_postponed_post_id=557,
        client_id=c.id,
        price=Decimal("350"),
    )
    db_session.add(plain)
    await db_session.flush()
    called = []

    async def spy(o, p):
        called.append(1)
        return {"success": True}

    await record_published(db_session, plain, pinner=spy, notify=False)
    assert called == []


@pytest.mark.asyncio
async def test_run_unpin_by_deadline(db_session):
    c = await _client(db_session)
    now = datetime(2026, 9, 11, 12, 0)
    due = AdPublication(
        client_id=c.id,
        community_vk_id=-100,
        vk_post_id=1,
        pinned_at=now - timedelta(hours=25),
        pinned_until=now - timedelta(hours=1),
    )
    fresh = AdPublication(
        client_id=c.id,
        community_vk_id=-100,
        vk_post_id=2,
        pinned_at=now - timedelta(hours=2),
        pinned_until=now + timedelta(hours=22),
    )
    done = AdPublication(
        client_id=c.id,
        community_vk_id=-100,
        vk_post_id=3,
        pinned_at=now - timedelta(days=3),
        pinned_until=now - timedelta(days=2),
        unpinned_at=now - timedelta(days=2),
    )
    db_session.add_all([due, fresh, done])
    await db_session.commit()

    class _F:
        def __call__(self):
            return self

        async def __aenter__(self):
            return db_session

        async def __aexit__(self, *a):
            return False

    calls = []

    async def unpinner(owner_id, post_id):
        calls.append((owner_id, post_id))
        return {"success": True}

    stats = await pinning.run_unpin(session_factory=_F(), unpinner=unpinner, now=now)
    assert stats == {"due": 1, "unpinned": 1, "failed": 0} and calls == [(-100, 1)]
    assert due.unpinned_at == now and fresh.unpinned_at is None
    again = await pinning.run_unpin(session_factory=_F(), unpinner=unpinner, now=now)
    assert again["due"] == 0


def test_unpin_task_registered():
    from tasks.celery_app import app

    assert "tasks.celery_app.unpin_ad_posts" in app.tasks
    assert app.conf.beat_schedule["unpin-ad-posts"]["task"] == "tasks.celery_app.unpin_ad_posts"


@pytest.mark.asyncio
async def test_record_published_notifies_client_with_first_photo(db_session, monkeypatch):
    """«📣 Ваш пост вышел» уходит с первой картинкой поста (Этап 5), без картинок — photos=[]."""
    from modules.ad_cabinet.vk_bot import notify as vk_notify

    c = await _client(db_session, author_vk_id=555)
    got = []

    async def fake_notify(session, client_id, text, **kw):
        got.append((client_id, text, kw))
        return True

    monkeypatch.setattr(vk_notify, "notify_client", fake_notify)

    async def no_pin(owner_id, post_id):
        return {"success": True}

    for i, names in enumerate((["a.jpg", "b.jpg"], [])):
        row = AdScheduledPost(
            community_vk_id=-100,
            text="t",
            publish_date=MSK_NOW + timedelta(days=i),
            status="scheduled",
            vk_postponed_post_id=7 + i,
            client_id=c.id,
            price=Decimal("0"),
            image_names=names,
        )
        db_session.add(row)
        await db_session.flush()
        await record_published(db_session, row, pinner=no_pin)
    assert [g[0] for g in got] == [c.id, c.id]
    assert "wall-100_7" in got[0][1] and got[0][2]["photos"] == ["a.jpg"]
    assert got[1][2]["photos"] == []
