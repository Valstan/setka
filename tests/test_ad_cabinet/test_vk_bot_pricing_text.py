"""Прайс, баланс и котировка ВК-бота с пакетом и скидкой — Этап 5, PR-3.

Чистые тексты (prices_text, quote_text, package_line) и сквозные сценарии на
in-memory БД: накопительная скидка видна в «Цены» и «Балансе», безлимит и пакет
показываются на шаге «когда» (0 ₽), долг блокирует до «Подтвердить», пакет
меньше выбора возвращает к районам, prepaid без оплаты — «ждёт подтверждения».
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from database.models import AdClient, AdClientPackage, AdPayment, Region
from modules.ad_cabinet import packages as pkgs
from modules.ad_cabinet.vk_bot import dialog

MSK_NOW = datetime(2026, 9, 15, 12, 0)
UTC_NOW = MSK_NOW - timedelta(hours=3)
TODAY = MSK_NOW.date()


def _btn(cmd):
    return dialog.Incoming(peer_id=500, payload={"cmd": cmd})


class _Submit:
    def __init__(self):
        self.calls = []

    async def __call__(self, session, client, draft):
        self.calls.append(dict(draft))
        return {"order_ref": "r", "price_total": 0, "posts": [1], "moderation": True}


async def _client(session):
    c = AdClient(name="К", author_vk_id=500, trusted=False)
    session.add(c)
    await session.flush()
    return c


async def _regions(session, n=2):
    rows = [
        Region(name=f"Р{i}", code=f"r{i}", vk_group_id=-(10 + i), is_active=True) for i in range(n)
    ]
    session.add_all(rows)
    await session.flush()
    return rows


async def _paid(session, client, n):
    for i in range(n):
        session.add(
            AdPayment(
                client_id=client.id,
                amount=Decimal("350"),
                status="paid",
                scheduled_post_id=1000 + i,
                paid_at=UTC_NOW - timedelta(days=1),
                paid_confirmed_at=UTC_NOW - timedelta(days=1),
            )
        )
    await session.flush()


def _when_state(regions, **draft):
    base = {
        "text": "t",
        "regions": [[r.id, r.name] for r in regions],
        "region_ids": [r.id for r in regions],
        "page": 0,
    }
    base.update(draft)
    return {"step": "order_when", "draft": base}


# ───────── чистые тексты ─────────


def test_prices_text_lists_everything_under_limit():
    t = dialog.prices_text()
    for needle in (
        "Закреп на сутки",
        "+200 ₽",
        "Безлимит",
        "30 дней",
        "5 000 ₽",
        "200 ₽",
        "минус 5 %",
    ):
        assert needle in t, needle
    assert len(t) < dialog.VK_MSG_MAX
    with_disc = dialog.prices_text(
        discount={"total": 10, "month": 10, "paid_month": 3, "next_step_posts": 2}
    )
    assert "10 %" in with_disc and "ещё 2" in with_disc
    capped = dialog.prices_text(
        discount={"total": 30, "month": 30, "paid_month": 18, "next_step_posts": 0}
    )
    assert "30 %" in capped and "ещё" not in capped.split("Ваша скидка")[1]
    unl = AdClientPackage(kind="unlimited", posts_total=0, period_end=TODAY + timedelta(days=5))
    assert "Безлимит до 20.09.2026" in dialog.prices_text(package=unl)


def test_quote_text_variants():
    q = {
        "price": 630,
        "base_price": 700,
        "discount_pct": 10,
        "floor_applied": False,
        "discount": {"month": 5, "next_step_posts": 1},
    }
    t = dialog.quote_text(q, n=2)
    assert "630 ₽" in t and "скидка 10 %" in t and "ещё 1 оплаченных" in t and "или в счёт" not in t
    floor = dialog.quote_text(
        {
            "price": 400,
            "base_price": 700,
            "discount_pct": 40,
            "floor_applied": True,
            "discount": {},
        },
        n=2,
    )
    assert "минимум 200 ₽" in floor
    pkg = AdClientPackage(kind="prepaid", posts_total=5, posts_used=0)
    assert "в счёт пакета" in dialog.quote_text(
        q, n=3, package=pkg, left=5
    ) and "останется 2 из 5" in dialog.quote_text(q, n=3, package=pkg, left=5)
    unl = AdClientPackage(kind="unlimited", posts_total=0, period_end=TODAY)
    assert "в счёт безлимита до 15.09.2026 (0 ₽)" in dialog.quote_text(
        q, n=3, package=unl, left=10**6
    )


# ───────── сквозные ─────────


@pytest.mark.asyncio
async def test_discount_visible_in_prices_and_balance(db_session):
    c = await _client(db_session)
    await _paid(db_session, c, 6)
    s = _Submit()
    replies, _, _ = await dialog.handle(db_session, _btn("prices"), None, submit=s, now_msk=MSK_NOW)
    assert "Ваша скидка сейчас: 10 %" in replies[0][0]
    replies, _, _ = await dialog.handle(
        db_session, _btn("balance"), None, submit=s, now_msk=MSK_NOW
    )
    assert "Ваша скидка: 10 %" in replies[0][0]


@pytest.mark.asyncio
async def test_unlimited_and_prepaid_quotes_on_when_step(db_session):
    c = await _client(db_session)
    rs = await _regions(db_session, 2)
    s = _Submit()
    ps, pe = pkgs.unlimited_period(TODAY)
    db_session.add(
        AdClientPackage(
            client_id=c.id,
            kind="unlimited",
            posts_total=0,
            posts_used=0,
            price=5000,
            period_start=ps,
            period_end=pe,
            is_active=True,
            paid_at=UTC_NOW,
        )
    )
    await db_session.flush()
    replies, state, _ = await dialog.handle(
        db_session, _btn("now"), _when_state(rs), submit=s, now_msk=MSK_NOW
    )
    assert (
        state["step"] == "order_confirm"
        and "в счёт безлимита" in replies[0][0]
        and "0 ₽" in replies[0][0]
    )
    assert "районов: 2" in replies[0][0]
    # дата вне периода безлимита — остаёмся на шаге «когда»
    replies, state, _ = await dialog.handle(
        db_session,
        dialog.Incoming(peer_id=500, text="25.10 14:30"),
        _when_state(rs),
        submit=s,
        now_msk=MSK_NOW,
    )
    assert state["step"] == "order_when" and "Пакет действует до 14.10.2026" in replies[0][0]
    # «Цены» и «Баланс» показывают безлимит
    replies, _, _ = await dialog.handle(db_session, _btn("prices"), None, submit=s, now_msk=MSK_NOW)
    assert "Безлимит до" in replies[0][0]
    replies, _, _ = await dialog.handle(
        db_session, _btn("balance"), None, submit=s, now_msk=MSK_NOW
    )
    assert "♾ Безлимит до" in replies[0][0]


@pytest.mark.asyncio
async def test_small_package_sends_back_to_regions(db_session):
    c = await _client(db_session)
    rs = await _regions(db_session, 2)
    s = _Submit()
    db_session.add(
        AdClientPackage(
            client_id=c.id,
            kind="prepaid",
            posts_total=1,
            posts_used=0,
            price=350,
            is_active=True,
            paid_at=UTC_NOW,
        )
    )
    await db_session.flush()
    replies, state, _ = await dialog.handle(
        db_session, _btn("now"), _when_state(rs), submit=s, now_msk=MSK_NOW
    )
    assert (
        state["step"] == "order_regions"
        and "осталось 1 постов, а районов выбрано 2" in replies[0][0]
    )
    assert "Р0" in replies[0][1]  # клавиатура районов
    # один район — в счёт пакета, останется 0
    replies, state, _ = await dialog.handle(
        db_session, _btn("now"), _when_state(rs[:1]), submit=s, now_msk=MSK_NOW
    )
    assert (
        state["step"] == "order_confirm"
        and "в счёт пакета" in replies[0][0]
        and "останется 0 из 1" in replies[0][0]
    )


@pytest.mark.asyncio
async def test_debt_blocks_before_confirm_and_prepaid_awaits_payment(db_session):
    c = await _client(db_session)
    rs = await _regions(db_session, 1)
    s = _Submit()
    db_session.add(
        AdClientPackage(
            client_id=c.id,
            kind="postpaid",
            posts_total=5,
            posts_used=5,
            price=1300,
            period_start=TODAY - timedelta(days=40),
            period_end=TODAY - timedelta(days=10),
            is_active=True,
            paid_at=None,
        )
    )
    await db_session.flush()
    replies, state, _ = await dialog.handle(
        db_session, _btn("now"), _when_state(rs), submit=s, now_msk=MSK_NOW
    )
    assert state is None and replies[0][0].startswith("⛔") and s.calls == []
    from sqlalchemy import select

    from database.models import AdInteraction

    kinds = [r.kind for r in (await db_session.execute(select(AdInteraction))).scalars().all()]
    assert "cabinet_order_refused" in kinds


@pytest.mark.asyncio
async def test_prepaid_without_payment_shown_in_balance(db_session):
    c = await _client(db_session)
    s = _Submit()
    db_session.add(
        AdClientPackage(
            client_id=c.id,
            kind="prepaid",
            posts_total=5,
            posts_used=0,
            price=1300,
            is_active=True,
            paid_at=None,
        )
    )
    await db_session.flush()
    replies, _, _ = await dialog.handle(
        db_session, _btn("balance"), None, submit=s, now_msk=MSK_NOW
    )
    assert "Пакет 5 постов ждёт подтверждения оплаты" in replies[0][0]
