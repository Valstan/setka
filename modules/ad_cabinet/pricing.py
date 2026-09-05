"""Цена заказа для конкретного клиента: прайс → скидки → пол (Этап 2, 2026-09-05).

Единственная точка, где к тарифу ``config.ad_landing.quote_price`` применяются
скидки клиента. Её зовут все три потребителя — заказ (``client_orders``),
предварительная котировка кабинета (``/api/advertiser/quote``) и ВК-бот —
поэтому число в форме, в подтверждении бота и в счёте одно и то же.

Оплаченные посты считаются по ``ad_payments`` со статусом ``paid``: строка за
размещение (``scheduled_post_id``) — один пост, платёж за пакет — ``units_paid``
постов. Месяц — календарный по МСК, окно по моменту подтверждения оплаты
(``paid_confirmed_at``, иначе ``paid_at``).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple

from sqlalchemy import case, func, select

from config.ad_landing import PIN_PRICE_RUB, apply_discount, discount_pct, quote_price
from database.models import AdPayment

MSK = timedelta(hours=3)


def _month_bounds_utc(now_msk: datetime) -> Tuple[datetime, datetime]:
    """Границы текущего МСК-месяца в UTC naive (как хранятся paid_*_at)."""
    start_msk = now_msk.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start_msk.month == 12:
        end_msk = start_msk.replace(year=start_msk.year + 1, month=1)
    else:
        end_msk = start_msk.replace(month=start_msk.month + 1)
    return start_msk - MSK, end_msk - MSK


async def count_paid_posts(
    session, client_id: int, *, now_msk: Optional[datetime] = None
) -> Tuple[int, int]:
    """``(оплачено постов в этом месяце, оплачено постов за всё время)``."""
    now_msk = now_msk or (datetime.utcnow() + MSK)
    start, end = _month_bounds_utc(now_msk)
    ts = func.coalesce(AdPayment.paid_confirmed_at, AdPayment.paid_at)
    units = case(
        (AdPayment.scheduled_post_id.isnot(None), 1),
        else_=func.coalesce(AdPayment.units_paid, 0),
    )
    row = (
        await session.execute(
            select(
                func.coalesce(func.sum(case(((ts >= start) & (ts < end), units), else_=0)), 0),
                func.coalesce(func.sum(units), 0),
            ).where(AdPayment.client_id == int(client_id), AdPayment.status == "paid")
        )
    ).one()
    return int(row[0] or 0), int(row[1] or 0)


async def client_discount(
    session, client_id: int, *, now_msk: Optional[datetime] = None
) -> Dict[str, int]:
    paid_month, paid_total = await count_paid_posts(session, client_id, now_msk=now_msk)
    d = discount_pct(paid_month, paid_total)
    return {**d, "paid_month": paid_month, "paid_total": paid_total}


async def quote_for_client(
    session,
    client_id: Optional[int],
    n: int,
    *,
    pinned: bool = False,
    now_msk: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Цена ``n`` размещений для клиента: тариф, скидка, пол, закреп.

    Возвращает надмножество ``quote_price``: ``base_price`` (по прайсу),
    ``discount`` (словарь ``client_discount``), ``price`` (к оплате без
    закрепа), ``pin_price`` (``n × PIN_PRICE_RUB`` при ``pinned``), ``total``.
    ``client_id=None`` — гость лендинга, без скидок.
    """
    base = quote_price(n)
    disc = (
        await client_discount(session, client_id, now_msk=now_msk)
        if client_id is not None
        else {
            "month": 0,
            "regular": 0,
            "total": 0,
            "next_step_posts": 3,
            "paid_month": 0,
            "paid_total": 0,
        }
    )
    applied = apply_discount(base["price"], base["n"], disc["total"])
    pin_price = PIN_PRICE_RUB * base["n"] if pinned else 0
    return {
        **base,
        "base_price": base["price"],
        "discount": disc,
        "discount_pct": applied["discount_pct"],
        "floor_applied": applied["floor_applied"],
        "price": applied["price"],
        "pinned": bool(pinned),
        "pin_price": pin_price,
        "total": applied["price"] + pin_price,
    }


__all__ = ["count_paid_posts", "client_discount", "quote_for_client", "MSK"]
