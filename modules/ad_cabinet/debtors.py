"""Трекинг должников рекламного кабинета (С4 программы ad-CRM).

«Должник» — клиент, у которого есть размещение с неоплаченным счётом
(``ad_payments.status='awaiting'``) старше порога ``DEBTOR_DAYS`` дней. Решение
владельца 2026-06-13: порог 3 дня, Telegram-напоминание раз в день, плюс
плашка/фильтр в кабинете. Полный авто-приём денег не делаем (банк-API) —
оператор отмечает оплату руками, а код лишь напоминает о просрочке.

``collect_debtors`` — чистая логика (свод просроченных awaiting по клиентам),
покрывается тестами без сети. ``run_debtor_alert`` — оркестрация суточного
Telegram-напоминания (``send`` инъектируем).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy import select

from database.models import AdClient, AdPayment

logger = logging.getLogger(__name__)

# Порог просрочки в днях (решение владельца — 3). Env-override на всякий случай.
DEBTOR_DAYS = int(os.getenv("AD_DEBTOR_DAYS", "3"))


async def collect_debtors(
    session,
    *,
    threshold_days: Optional[int] = None,
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Свод должников: клиенты с awaiting-оплатами старше порога.

    Возвращает список ``{client_id, name, amount, count, oldest_days}``,
    отсортированный по «дольше всех не платит» (oldest_days убыв.).
    """
    threshold_days = DEBTOR_DAYS if threshold_days is None else threshold_days
    now = now or datetime.utcnow()
    cutoff = now - timedelta(days=threshold_days)

    rows = (
        await session.execute(
            select(AdPayment, AdClient)
            .join(AdClient, AdPayment.client_id == AdClient.id)
            .where(
                AdPayment.status == "awaiting",
                AdPayment.created_at <= cutoff,
            )
            .order_by(AdPayment.created_at.asc())
        )
    ).all()

    by_client: Dict[int, Dict[str, Any]] = {}
    for pay, client in rows:
        d = by_client.setdefault(
            client.id,
            {
                "client_id": client.id,
                "name": client.name or f"vk{client.author_vk_id}",
                "amount": 0.0,
                "count": 0,
                "oldest_days": 0,
            },
        )
        d["amount"] += float(pay.amount or 0)
        d["count"] += 1
        if pay.created_at:
            age = (now - pay.created_at).days
            if age > d["oldest_days"]:
                d["oldest_days"] = age

    return sorted(by_client.values(), key=lambda x: x["oldest_days"], reverse=True)


def format_debtor_alert(debtors: List[Dict[str, Any]], threshold_days: int, url: str = "") -> str:
    """Текст Telegram-напоминания о должниках."""
    total = sum(float(d["amount"]) for d in debtors)
    lines = [
        f"💰 Должники по рекламе (неоплачено > {threshold_days} дн.): "
        f"<b>{len(debtors)}</b> на <b>{int(total)} ₽</b>"
    ]
    for d in debtors[:20]:
        lines.append(f"• {d['name']}: {int(d['amount'])} ₽, ждём {d['oldest_days']} дн.")
    if len(debtors) > 20:
        lines.append(f"…и ещё {len(debtors) - 20}")
    if url:
        lines.append(f"\n{url}")
    return "\n".join(lines)


async def run_debtor_alert(
    *,
    session_factory: Optional[Callable] = None,
    send: Optional[Callable[[str], None]] = None,
    threshold_days: Optional[int] = None,
    now: Optional[datetime] = None,
    url: str = "",
) -> Dict[str, Any]:
    """Собрать должников и отправить одно Telegram-напоминание (если есть)."""
    if session_factory is None:
        from database.connection import AsyncSessionLocal

        session_factory = AsyncSessionLocal
    threshold_days = DEBTOR_DAYS if threshold_days is None else threshold_days

    async with session_factory() as session:
        debtors = await collect_debtors(session, threshold_days=threshold_days, now=now)

    if not debtors:
        return {"debtors": 0, "alerted": False}

    if send is not None:
        try:
            send(format_debtor_alert(debtors, threshold_days, url))
        except Exception as e:  # pragma: no cover - защита
            logger.warning("debtor alert send failed: %s", e)
            return {"debtors": len(debtors), "alerted": False}
        return {"debtors": len(debtors), "alerted": True}

    return {"debtors": len(debtors), "alerted": False}
