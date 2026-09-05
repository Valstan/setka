"""Пакет «безлимит 30 дней» (Этап 2, PR 2B; решение владельца 2026-09-05).

- открывается галочкой «оплачен» (она же ставит период 30 дней с даты);
- квоты нет: consume не упирается в posts_total, использованное считается;
- порядок списания промо → безлимит → платный; исчерпание периода не блокирует;
- заказ на много районов проходит, «1 пост в сутки в сообщество» держится;
- потолок незавершённых постов у безлимитчика выше;
- продление — следующие 30 дней встык, повтор → 409; to_dict несёт unlimited.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from database.models import AdClient, AdClientPackage, AdScheduledPost, Region
from modules.ad_cabinet import client_orders
from modules.ad_cabinet import packages as pkgs

MSK_NOW = datetime(2026, 9, 10, 12, 0)
TODAY = MSK_NOW.date()


def _unl(client_id, *, paid=True, start=None, price=5000, active=True):
    if paid and start is None:
        start = TODAY
    ps, pe = pkgs.unlimited_period(start) if start else (None, None)
    return AdClientPackage(
        client_id=client_id,
        kind="unlimited",
        posts_total=0,
        posts_used=0,
        price=price,
        period_start=ps,
        period_end=pe,
        is_active=active,
        paid_at=datetime(2026, 9, 10) if paid else None,
    )


async def _client(session):
    c = AdClient(name="Безлимитчик", trusted=False)
    session.add(c)
    await session.flush()
    return c


def test_unlimited_period_is_30_days():
    s, e = pkgs.unlimited_period(date(2026, 9, 10))
    assert (e - s).days == 29 and e == date(2026, 10, 9)


@pytest.mark.asyncio
async def test_unpaid_unlimited_is_not_available(db_session):
    c = await _client(db_session)
    db_session.add(_unl(c.id, paid=False))
    await db_session.flush()
    state = await pkgs.get_state(db_session, c.id, today=TODAY)
    assert state["package"] is None and state["block_reason"] is None
    assert await pkgs.has_unlimited(db_session, c.id, today=TODAY) is False


@pytest.mark.asyncio
async def test_paid_unlimited_available_within_period_only(db_session):
    c = await _client(db_session)
    p = _unl(c.id)
    db_session.add(p)
    await db_session.flush()
    assert (await pkgs.get_state(db_session, c.id, today=TODAY))["package"].id == p.id
    assert (await pkgs.get_state(db_session, c.id, today=TODAY + timedelta(days=29)))[
        "package"
    ].id == p.id
    after = await pkgs.get_state(db_session, c.id, today=TODAY + timedelta(days=30))
    assert after["package"] is None and after["block_reason"] is None  # прайс, не блок
    assert await pkgs.has_unlimited(db_session, c.id, today=TODAY) is True


@pytest.mark.asyncio
async def test_consume_has_no_cap_but_counts(db_session):
    c = await _client(db_session)
    p = _unl(c.id)
    db_session.add(p)
    await db_session.flush()
    assert await pkgs.consume(db_session, p, 38) is True
    assert await pkgs.consume(db_session, p, 38) is True
    assert p.posts_used == 76 and p.to_dict()["posts_left"] is None and p.to_dict()["unlimited"]


@pytest.mark.asyncio
async def test_promo_first_then_unlimited_then_prepaid(db_session):
    c = await _client(db_session)
    prepaid = AdClientPackage(
        client_id=c.id, kind="prepaid", posts_total=5, price=1500, paid_at=datetime(2026, 9, 1)
    )
    db_session.add(prepaid)
    unl = _unl(c.id)
    db_session.add(unl)
    await db_session.flush()
    assert (await pkgs.get_state(db_session, c.id, today=TODAY))["package"].id == unl.id
    promo = AdClientPackage(
        client_id=c.id, kind="free_promo", posts_total=3, paid_at=datetime(2026, 9, 1)
    )
    db_session.add(promo)
    await db_session.flush()
    assert (await pkgs.get_state(db_session, c.id, today=TODAY))["package"].id == promo.id


@pytest.mark.asyncio
async def test_order_many_regions_free_and_daily_slot_still_enforced(db_session):
    regions = [
        Region(name=f"Р{i}", code=f"r{i}", vk_group_id=-(100 + i), is_active=True)
        for i in range(12)
    ]
    db_session.add_all(regions)
    c = await _client(db_session)
    db_session.add(_unl(c.id))
    await db_session.flush()

    async def _no_att(*a, **k):
        return []

    kw = dict(
        client=c,
        user_id=1,
        text="реклама",
        image_paths=[],
        publish_at=MSK_NOW + timedelta(days=1),
        publish_now=False,
        publisher_factory=lambda: None,
        attachment_builder=_no_att,
        msk_to_unix=lambda d: 0,
        now=MSK_NOW,
    )
    res = await client_orders.submit_order(db_session, region_ids=[r.id for r in regions], **kw)
    assert (
        res["price_total"] == 0 and res["quote"]["kind"] == "unlimited" and len(res["posts"]) == 12
    )
    with pytest.raises(client_orders.OrderError):  # тот же день, те же сообщества
        await client_orders.submit_order(db_session, region_ids=[regions[0].id], **kw)
    res2 = await client_orders.submit_order(
        db_session, region_ids=[regions[0].id], **{**kw, "publish_at": MSK_NOW + timedelta(days=2)}
    )
    assert res2["price_total"] == 0


@pytest.mark.asyncio
async def test_active_cap_is_higher_for_unlimited(db_session, monkeypatch):
    r = Region(name="Р", code="r", vk_group_id=-100, is_active=True)
    db_session.add(r)
    c = await _client(db_session)
    db_session.add(_unl(c.id))
    for i in range(3):
        db_session.add(
            AdScheduledPost(
                community_vk_id=-100,
                text="t",
                publish_date=MSK_NOW + timedelta(days=10 + i),
                status="scheduled",
                client_id=c.id,
                price=0,
            )
        )
    await db_session.flush()
    monkeypatch.setattr(client_orders, "MAX_ACTIVE_POSTS", 2)
    monkeypatch.setattr(client_orders, "MAX_ACTIVE_POSTS_UNLIMITED", 50)

    async def _no_att(*a, **k):
        return []

    res = await client_orders.submit_order(
        db_session,
        client=c,
        user_id=1,
        text="реклама",
        image_paths=[],
        region_ids=[r.id],
        publish_at=MSK_NOW + timedelta(days=1),
        publish_now=False,
        publisher_factory=lambda: None,
        attachment_builder=_no_att,
        msk_to_unix=lambda d: 0,
        now=MSK_NOW,
    )
    assert len(res["posts"]) == 1  # обычный потолок 2 не мешает безлимитчику


@pytest.mark.asyncio
async def test_crm_mark_paid_sets_period_and_extend_chains(db_session, monkeypatch):
    from web.api import ad_crm

    c = await _client(db_session)
    p = _unl(c.id, paid=False)
    db_session.add(p)
    await db_session.commit()
    monkeypatch.setattr(ad_crm, "_msk_today", lambda: TODAY)
    monkeypatch.setattr(
        "modules.ad_cabinet.vk_bot.notify.notify_client",
        AsyncMock(return_value=False),
        raising=False,
    )

    out = await ad_crm.package_mark_paid(p.id, db=db_session)
    assert out["paid"] and out["period_start"] == TODAY.isoformat()
    assert out["period_end"] == (TODAY + timedelta(days=29)).isoformat()

    nxt = await ad_crm.package_extend(p.id, db=db_session)
    assert (
        nxt["kind"] == "unlimited"
        and nxt["period_start"] == (TODAY + timedelta(days=30)).isoformat()
    )
    assert nxt["period_end"] == (TODAY + timedelta(days=59)).isoformat() and not nxt["paid"]
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as e:
        await ad_crm.package_extend(p.id, db=db_session)
    assert e.value.status_code == 409


@pytest.mark.asyncio
async def test_crm_create_unlimited_paid_starts_today(db_session, monkeypatch):
    from fastapi import HTTPException

    from web.api import ad_crm

    c = await _client(db_session)
    monkeypatch.setattr(ad_crm, "_msk_today", lambda: TODAY)
    out = await ad_crm.create_package(
        c.id,
        ad_crm.PackageIn(kind="unlimited", posts_total=0, price=5000, paid=True),
        db=db_session,
    )
    assert out["unlimited"] and out["posts_total"] == 0
    assert out["period_start"] == TODAY.isoformat()
    with pytest.raises(HTTPException) as e:  # обычному пакету нужна квота ≥ 1
        await ad_crm.create_package(
            c.id, ad_crm.PackageIn(kind="prepaid", posts_total=0, price=100), db=db_session
        )
    assert e.value.status_code == 400
