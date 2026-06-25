"""Напоминание о перерасходе пакета публикаций (И2 непрерывной нити клиента).

Клиент «перерасходовал», когда вышло БОЛЬШЕ публикаций, чем оплачено пакетом
(Σ ``ad_payments.units_paid`` при ``status='paid'`` < число ``published``-публикаций).
Решение владельца 2026-06-25: учёт в штуках, напоминать **при перерасходе** (вышло
больше оплаченного) — чтобы вовремя напомнить рекламодателю о проплате следующего
пакета, не теряя клиента из вида.

Дедуп: ``ad_clients.spend_alerted_at`` (ставим при отправке, сбрасываем в NULL при
новой оплате — см. ``create_payment``/``update_payment``). Без штучного пакета
(``units_paid`` не задан → ``paid_units=0``) клиент перерасходовавшим не считается:
фича включается по мере того, как оператор начинает фиксировать «за сколько
публикаций» оплату.

``collect_overspent`` — чистая логика (тесты без сети); ``run_overspend_alert`` —
оркестрация суточного Telegram-напоминания (``send`` инъектируем).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy import func, select, update

from database.models import AdClient, AdPayment, AdPublication

logger = logging.getLogger(__name__)

# Кулдаун повторного напоминания о перерасходе того же клиента (дни). Дедуп —
# по spend_alerted_at: повторно тревожим, только если прошло больше кулдауна
# (или клиент доплатил → spend_alerted_at сброшен в NULL).
SPEND_ALERT_COOLDOWN_DAYS = int(os.getenv("AD_SPEND_ALERT_COOLDOWN_DAYS", "3"))


async def collect_overspent(
    session,
    *,
    now: Optional[datetime] = None,
    cooldown_days: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Свод клиентов с перерасходом пакета (вышло > оплачено), не на кулдауне.

    Возвращает ``{client_id, name, paid_units, consumed, over}``,
    отсортированный по величине перебора (``over`` убыв.).
    """
    now = now or datetime.utcnow()
    cooldown_days = SPEND_ALERT_COOLDOWN_DAYS if cooldown_days is None else cooldown_days
    cutoff = now - timedelta(days=cooldown_days)

    paid_units_sq = (
        select(func.coalesce(func.sum(AdPayment.units_paid), 0))
        .where(AdPayment.client_id == AdClient.id, AdPayment.status == "paid")
        .scalar_subquery()
    )
    consumed_sq = (
        select(func.count(AdPublication.id))
        .where(
            AdPublication.client_id == AdClient.id,
            AdPublication.status == "published",
        )
        .scalar_subquery()
    )
    rows = (
        await session.execute(
            select(
                AdClient,
                paid_units_sq.label("paid_units"),
                consumed_sq.label("consumed"),
            ).where(
                paid_units_sq > 0
            )  # только клиенты с зафиксированным пакетом
        )
    ).all()

    out: List[Dict[str, Any]] = []
    for client, paid_units, consumed in rows:
        paid_units = int(paid_units or 0)
        consumed = int(consumed or 0)
        if consumed <= paid_units:
            continue  # пакет ещё не исчерпан — не перерасход
        if client.spend_alerted_at and client.spend_alerted_at > cutoff:
            continue  # уже напоминали недавно (дедуп)
        out.append(
            {
                "client_id": client.id,
                "name": client.name or f"vk{client.author_vk_id}",
                "paid_units": paid_units,
                "consumed": consumed,
                "over": consumed - paid_units,
            }
        )
    return sorted(out, key=lambda x: x["over"], reverse=True)


def format_overspent_alert(items: List[Dict[str, Any]], url: str = "") -> str:
    """Текст Telegram-напоминания о перерасходе пакетов."""
    lines = [
        f"📣 Перерасход пакета (вышло больше оплаченного): "
        f"<b>{len(items)}</b> — напомнить о проплате следующего периода"
    ]
    for d in items[:20]:
        lines.append(
            f"• {d['name']}: оплачено {d['paid_units']}, вышло {d['consumed']} "
            f"(перебор +{d['over']})"
        )
    if len(items) > 20:
        lines.append(f"…и ещё {len(items) - 20}")
    if url:
        lines.append(f"\n{url}")
    return "\n".join(lines)


async def run_overspend_alert(
    *,
    session_factory: Optional[Callable] = None,
    send: Optional[Callable[[str], None]] = None,
    now: Optional[datetime] = None,
    url: str = "",
) -> Dict[str, Any]:
    """Собрать перерасход и отправить одно Telegram-напоминание (если есть).

    После успешной отправки помечает клиентов ``spend_alerted_at=now`` (дедуп).
    """
    if session_factory is None:
        from database.connection import AsyncSessionLocal

        session_factory = AsyncSessionLocal
    now = now or datetime.utcnow()

    async with session_factory() as session:
        items = await collect_overspent(session, now=now)
        if not items:
            return {"overspent": 0, "alerted": False}

        if send is None:
            return {"overspent": len(items), "alerted": False}

        try:
            send(format_overspent_alert(items, url))
        except Exception as e:  # pragma: no cover - защита
            logger.warning("overspend alert send failed: %s", e)
            return {"overspent": len(items), "alerted": False}

        # Пометить отправленным — дедуп до доплаты/кулдауна.
        ids = [d["client_id"] for d in items]
        await session.execute(
            update(AdClient).where(AdClient.id.in_(ids)).values(spend_alerted_at=now)
        )
        await session.commit()
        return {"overspent": len(items), "alerted": True}
