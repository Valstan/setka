"""«Я оплатил» и подтверждение оплаты заказом (PR 1.7 аудита кабинета 2026-09-05).

- claim ставит claimed_at только на ожидающие незаявленные счета, пишет событие
  actor=client, повтор ничего не плодит;
- подмножество по payment_ids; чужие счета не трогаются;
- confirm_order переводит все awaiting заказа в paid одной записью в журнал,
  ставит стадию, банк, перевзводит перерасход, оплаченные не трогает;
- confirm_client — все ожидающие клиента; пустой набор — 0;
- список кабинетов несёт awaiting_total / claimed_total;
- доверие считается по РАЗНЫМ заказам, а не по постам.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from database.models import AdClient, AdInteraction, AdPayment, AdScheduledPost
from modules.ad_cabinet import cabinet_list, client_orders, payment_claims

NOW = datetime(2026, 9, 5, 9, 0, 0)


async def _client(session, **kw):
    c = AdClient(name=kw.pop("name", "Клиент"), stage="detected", **kw)
    session.add(c)
    await session.flush()
    return c


async def _post(session, client, *, order_ref, day):
    p = AdScheduledPost(
        community_vk_id=-100,
        text="t",
        publish_date=NOW + timedelta(days=day),
        status="published",
        client_id=client.id,
        order_ref=order_ref,
        price=Decimal("350.00"),
    )
    session.add(p)
    await session.flush()
    return p


async def _pay(session, client, amount, *, post=None, status="awaiting", claimed=None):
    p = AdPayment(
        client_id=client.id,
        amount=Decimal(str(amount)),
        status=status,
        scheduled_post_id=post.id if post is not None else None,
        claimed_at=claimed,
        paid_at=NOW,
    )
    session.add(p)
    await session.flush()
    return p


async def _events(session, kind):
    return (
        (await session.execute(select(AdInteraction).where(AdInteraction.kind == kind)))
        .scalars()
        .all()
    )


@pytest.mark.asyncio
async def test_claim_marks_awaiting_once_and_logs_client_event(db_session):
    c = await _client(db_session)
    a = await _pay(db_session, c, 350)
    b = await _pay(db_session, c, 200)
    paid = await _pay(db_session, c, 500, status="paid")

    res = await payment_claims.claim_payments(db_session, c, now=NOW)
    assert res["claimed"] == 2 and res["amount"] == 550.0
    assert sorted(res["payment_ids"]) == sorted([a.id, b.id])
    assert a.claimed_at == NOW and b.claimed_at == NOW and paid.claimed_at is None

    again = await payment_claims.claim_payments(db_session, c, now=NOW + timedelta(hours=1))
    assert again["claimed"] == 0 and a.claimed_at == NOW  # повтор не переставляет метку
    ev = await _events(db_session, "payment_claimed")
    assert len(ev) == 1 and ev[0].actor == "client" and "550" in ev[0].summary


@pytest.mark.asyncio
async def test_claim_subset_and_foreign_ids_ignored(db_session):
    c = await _client(db_session)
    other = await _client(db_session, name="Чужой")
    mine = await _pay(db_session, c, 350)
    mine2 = await _pay(db_session, c, 350)
    foreign = await _pay(db_session, other, 350)

    res = await payment_claims.claim_payments(
        db_session, c, payment_ids=[mine.id, foreign.id], now=NOW
    )
    assert res["payment_ids"] == [mine.id]
    assert mine.claimed_at == NOW and mine2.claimed_at is None and foreign.claimed_at is None
    assert (await payment_claims.claim_payments(db_session, c, payment_ids=[]))["claimed"] == 0


@pytest.mark.asyncio
async def test_confirm_order_marks_all_awaiting_of_the_order(db_session):
    c = await _client(db_session)
    c.spend_alerted_at = NOW - timedelta(days=1)
    p1 = await _post(db_session, c, order_ref="ord-1", day=1)
    p2 = await _post(db_session, c, order_ref="ord-1", day=2)
    p3 = await _post(db_session, c, order_ref="ord-2", day=3)
    a = await _pay(db_session, c, 350, post=p1, claimed=NOW)
    b = await _pay(db_session, c, 350, post=p2)
    already = await _pay(db_session, c, 350, post=p1, status="paid")
    other_order = await _pay(db_session, c, 350, post=p3)

    res = await payment_claims.confirm_order(db_session, "ord-1", bank="Сбербанк", now=NOW)
    assert res["confirmed"] == 2 and res["amount"] == 700.0 and res["client_id"] == c.id
    assert a.status == b.status == "paid"
    assert a.paid_confirmed_at == NOW and a.bank == "Сбербанк"
    assert already.paid_confirmed_at is None  # уже оплаченную не трогаем
    assert other_order.status == "awaiting"
    assert c.stage == "paid" and c.spend_alerted_at is None
    ev = await _events(db_session, "payment_paid")
    assert len(ev) == 1 and "700" in ev[0].summary and "заказ целиком" in ev[0].summary

    assert (await payment_claims.confirm_order(db_session, "ord-1"))["confirmed"] == 0
    assert (await payment_claims.confirm_order(db_session, "no-such"))["confirmed"] == 0


@pytest.mark.asyncio
async def test_confirm_client_takes_every_awaiting_and_keeps_lost_stage(db_session):
    c = await _client(db_session)
    c.stage = "lost"
    await _pay(db_session, c, 100)
    await _pay(db_session, c, 150)
    stranger = await _client(db_session, name="Другой")
    s_pay = await _pay(db_session, stranger, 999)

    res = await payment_claims.confirm_client(db_session, c.id, now=NOW)
    assert res["confirmed"] == 2 and res["amount"] == 250.0
    assert c.stage == "lost"  # потерянного не воскрешаем автоматически
    assert s_pay.status == "awaiting"
    assert (await payment_claims.confirm_client(db_session, stranger.id + 100))["confirmed"] == 0


@pytest.mark.asyncio
async def test_cabinet_list_shows_awaiting_and_claimed(db_session):
    c = await _client(db_session)
    await _pay(db_session, c, 350, claimed=NOW)
    await _pay(db_session, c, 200)
    await _pay(db_session, c, 1000, status="paid")
    # Клиент без аккаунта попадает в список только через собственное действие.
    await payment_claims.claim_payments(db_session, c, payment_ids=[], now=NOW)
    from modules.ad_cabinet.interaction_log import log_interaction

    log_interaction(db_session, kind="payment_claimed", client_id=c.id, actor="client")
    await db_session.commit()

    rows = await cabinet_list.list_cabinets(db_session)
    row = next(r for r in rows if r["id"] == c.id)
    assert row["paid_total"] == 1000.0
    assert row["awaiting_total"] == 550.0
    assert row["claimed_total"] == 350.0


@pytest.mark.asyncio
async def test_trust_counts_distinct_orders_not_posts(db_session):
    c = await _client(db_session)
    for day in range(3):
        await _post(db_session, c, order_ref="one-order", day=day)
    assert await client_orders.approved_orders_count(db_session, c.id) == 1
    await _post(db_session, c, order_ref="second", day=5)
    legacy = await _post(db_session, c, order_ref=None, day=6)  # старая строка без ref
    assert await client_orders.approved_orders_count(db_session, c.id) == 3
    legacy.status = "cancelled"
    await db_session.flush()
    assert await client_orders.approved_orders_count(db_session, c.id) == 2


def test_claim_and_confirm_routes_registered():
    import main

    paths = {getattr(r, "path", None) for r in main.app.routes}
    assert "/api/advertiser/payments/claim" in paths
    assert "/api/ad-crm/orders/{order_ref}/confirm-paid" in paths
    assert "/api/ad-crm/clients/{client_id}/payments/confirm-all" in paths
