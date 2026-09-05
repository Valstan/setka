"""Скидки клиента и пол цены (Этап 2, 2026-09-05).

- оплаченные посты: строка за размещение = 1, платёж за пакет = units_paid;
  месяц — календарный МСК по моменту подтверждения;
- 5 % за каждые 3 оплаченных поста в месяце, потолок 30 %, +10 % постоянным;
- пол 200 ₽ за размещение не поднимает цену выше прайса («вся сеть» 5000);
- заказ (submit_order) считает ту же цену, что и котировка.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from config.ad_landing import PRICE_SINGLE_RUB, apply_discount, discount_pct, quote_price
from database.models import AdClient, AdPayment, Region
from modules.ad_cabinet import client_orders, pricing

MSK_NOW = datetime(2026, 9, 15, 12, 0, 0)  # МСК
UTC_NOW = MSK_NOW - timedelta(hours=3)


async def _client(session):
    c = AdClient(name="К", trusted=False)
    session.add(c)
    await session.flush()
    return c


async def _paid(session, client, *, confirmed_utc, post_id=None, units=None, status="paid"):
    p = AdPayment(
        client_id=client.id,
        amount=Decimal("350"),
        status=status,
        scheduled_post_id=post_id,
        units_paid=units,
        paid_at=confirmed_utc,
        paid_confirmed_at=confirmed_utc,
    )
    session.add(p)
    await session.flush()
    return p


# ───────── чистые правила ─────────


def test_discount_steps_and_caps():
    assert discount_pct(0, 0)["total"] == 0
    assert discount_pct(2, 2) == {"month": 0, "regular": 0, "total": 0, "next_step_posts": 1}
    assert discount_pct(3, 3)["month"] == 5 and discount_pct(3, 3)["next_step_posts"] == 3
    assert discount_pct(17, 17)["month"] == 25
    assert discount_pct(18, 18)["month"] == 30 and discount_pct(18, 18)["next_step_posts"] == 0
    assert discount_pct(99, 99)["month"] == 30  # потолок
    assert discount_pct(0, 10)["regular"] == 10 and discount_pct(0, 9)["regular"] == 0
    assert discount_pct(18, 40)["total"] == 40  # складываются


def test_apply_discount_respects_floor_and_cap():
    assert apply_discount(700, 2, 5) == {
        "price": 665,
        "discount_pct": 5,
        "floor_applied": False,
        "saved": 35,
    }
    # 40 % от 350 = 210 ≥ пола 200 — пол не нужен; 40 % от 2×350 = 420 ≥ 400 тоже.
    assert apply_discount(350, 1, 40)["price"] == 210
    # Пол: 3 поста по прайсу 1050, скидка 40 % → 630 > 600, но 45 % → 577 < 600 → 600.
    r = apply_discount(1050, 3, 45)
    assert r["price"] == 600 and r["floor_applied"] is True
    # «Вся сеть»: 5000 за 38 сообществ дешевле пола 7600 — пол не поднимает цену.
    whole = quote_price(38)["price"]
    assert apply_discount(whole, 38, 0)["price"] == whole
    assert apply_discount(whole, 38, 40)["price"] == whole  # пол = min(база, 200·n) = база
    assert apply_discount(0, 0, 30)["price"] == 0


# ───────── подсчёт по платежам ─────────


@pytest.mark.asyncio
async def test_count_paid_posts_month_window_and_units(db_session):
    c = await _client(db_session)
    await _paid(db_session, c, confirmed_utc=UTC_NOW - timedelta(days=1), post_id=1)
    await _paid(db_session, c, confirmed_utc=UTC_NOW - timedelta(days=2), post_id=2)
    await _paid(db_session, c, confirmed_utc=UTC_NOW - timedelta(days=3), units=5)  # пакет
    await _paid(db_session, c, confirmed_utc=UTC_NOW - timedelta(days=40), post_id=3)  # прошлый
    await _paid(db_session, c, confirmed_utc=UTC_NOW, post_id=4, status="awaiting")  # не оплачен
    # 1-е число 00:30 МСК = 31-е 21:30 UTC прошлого месяца — но это уже ЭТОТ месяц по МСК.
    await _paid(db_session, c, confirmed_utc=datetime(2026, 8, 31, 21, 30), post_id=5)
    month, total = await pricing.count_paid_posts(db_session, c.id, now_msk=MSK_NOW)
    assert month == 1 + 1 + 5 + 1 == 8
    assert total == 9


@pytest.mark.asyncio
async def test_quote_for_client_applies_discount_and_pin(db_session):
    c = await _client(db_session)
    for i in range(6):  # 6 оплаченных постов в месяце → 10 %
        await _paid(db_session, c, confirmed_utc=UTC_NOW - timedelta(hours=i + 1), post_id=i)
    q = await pricing.quote_for_client(db_session, c.id, 2, pinned=True, now_msk=MSK_NOW)
    assert q["base_price"] == 2 * PRICE_SINGLE_RUB and q["discount_pct"] == 10
    assert q["price"] == 630 and q["pin_price"] == 400 and q["total"] == 1030
    assert q["discount"]["paid_month"] == 6 and q["discount"]["next_step_posts"] == 3

    guest = await pricing.quote_for_client(db_session, None, 2)
    assert guest["price"] == 700 and guest["discount_pct"] == 0


@pytest.mark.asyncio
async def test_submit_order_charges_discounted_price(db_session):
    r = Region(name="Малмыж", code="mi", vk_group_id=-100, is_active=True)
    db_session.add(r)
    c = await _client(db_session)
    for i in range(3):
        await _paid(db_session, c, confirmed_utc=UTC_NOW - timedelta(hours=i + 1), post_id=i)
    await db_session.flush()

    async def _no_attachments(*a, **k):
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
        attachment_builder=_no_attachments,
        msk_to_unix=lambda d: 0,
        now=MSK_NOW,
    )
    assert res["price_total"] == 332.0  # 350 − 5 %, округление до рубля
    assert res["quote"]["discount_pct"] == 5 and res["quote"]["base_price"] == 350
    assert float(res["posts"][0].price) == 332.0
