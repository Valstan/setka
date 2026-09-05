"""«Я оплатил» и подтверждение оплаты заказом (PR 1.7 аудита кабинета 2026-09-05).

До этого клиент переводил деньги и писал об этом в чат (или никуда), а владелец
подтверждал каждую строку ``ad_payments`` по одной — для заказа Анны это
15 кликов. Здесь две операции, обе без commit (коммитит вызывающий):

- :func:`claim_payments` — клиент заявляет оплату: ``claimed_at`` на ожидающих
  счетах, событие ``payment_claimed`` (actor=client). Идемпотентна: уже
  заявленные и оплаченные строки не трогаются.
- :func:`confirm_payments` / :func:`confirm_order` / :func:`confirm_client` —
  владелец подтверждает пачку: ``awaiting → paid``, ``paid_confirmed_at``,
  банк, стадия клиента ``paid``, перевзвод напоминания о перерасходе, одно
  событие ``payment_paid`` на пачку. Тот же переход, что у операторской правки
  одной строки (``web/api/ad_crm.update_payment``).

Уведомления (пинг владельцу, «спасибо» клиенту) — на уровне API/бота.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence

from sqlalchemy import select

from database.models import AdClient, AdPayment, AdScheduledPost
from modules.ad_cabinet.interaction_log import log_interaction


def _amount(rows: Iterable[AdPayment]) -> float:
    return float(sum(float(r.amount or 0) for r in rows))


async def claim_payments(
    session,
    client: AdClient,
    *,
    payment_ids: Optional[Sequence[int]] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Клиент нажал «Я оплатил». ``payment_ids`` — подмножество, ``None`` — все."""
    now = now or datetime.utcnow()
    q = select(AdPayment).where(
        AdPayment.client_id == client.id,
        AdPayment.status == "awaiting",
        AdPayment.claimed_at.is_(None),
    )
    if payment_ids is not None:
        ids = [int(i) for i in payment_ids]
        if not ids:
            return {"claimed": 0, "amount": 0.0, "payment_ids": []}
        q = q.where(AdPayment.id.in_(ids))
    rows: List[AdPayment] = list((await session.execute(q.with_for_update())).scalars().all())
    if not rows:
        return {"claimed": 0, "amount": 0.0, "payment_ids": []}
    for r in rows:
        r.claimed_at = now
    total = _amount(rows)
    log_interaction(
        session,
        kind="payment_claimed",
        client_id=client.id,
        payment_id=rows[0].id,
        summary=f"Клиент сообщил об оплате: {total:g} ₽ ({len(rows)} сч.)",
        meta={"amount": total, "payment_ids": [r.id for r in rows]},
        actor="client",
    )
    return {"claimed": len(rows), "amount": total, "payment_ids": [r.id for r in rows]}


async def confirm_payments(
    session,
    rows: Sequence[AdPayment],
    *,
    bank: Optional[str] = None,
    now: Optional[datetime] = None,
    summary_suffix: str = "",
) -> Dict[str, Any]:
    """Ядро подтверждения: только ``awaiting``-строки из ``rows`` становятся ``paid``."""
    now = now or datetime.utcnow()
    todo = [r for r in rows if r.status == "awaiting"]
    if not todo:
        return {"confirmed": 0, "amount": 0.0, "client_id": None, "payment_ids": []}
    for r in todo:
        r.status = "paid"
        r.paid_confirmed_at = now
        if bank:
            r.bank = bank
    client_id = todo[0].client_id
    client = await session.get(AdClient, client_id) if client_id else None
    if client is not None:
        if client.stage != "lost":
            client.stage = "paid"
        client.spend_alerted_at = None  # доплата перевзводит напоминание (И2)
    total = _amount(todo)
    log_interaction(
        session,
        kind="payment_paid",
        client_id=client_id,
        payment_id=todo[0].id,
        summary=f"Оплата подтверждена: {total:g} ₽ ({len(todo)} сч.){summary_suffix}"
        + (f", {bank}" if bank else ""),
        meta={"amount": total, "bank": bank, "payment_ids": [r.id for r in todo]},
    )
    return {
        "confirmed": len(todo),
        "amount": total,
        "client_id": client_id,
        "payment_ids": [r.id for r in todo],
    }


async def confirm_order(
    session,
    order_ref: str,
    *,
    bank: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Подтвердить все ожидающие счета заказа (по ``ad_scheduled_posts.order_ref``)."""
    post_ids = select(AdScheduledPost.id).where(AdScheduledPost.order_ref == str(order_ref)[:36])
    rows = (
        (
            await session.execute(
                select(AdPayment)
                .where(
                    AdPayment.status == "awaiting",
                    AdPayment.scheduled_post_id.in_(post_ids),
                )
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    return await confirm_payments(
        session, rows, bank=bank, now=now, summary_suffix=" — заказ целиком"
    )


async def confirm_client(
    session,
    client_id: int,
    *,
    bank: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Подтвердить ВСЕ ожидающие счета клиента (кнопка «всё оплачено»)."""
    rows = (
        (
            await session.execute(
                select(AdPayment)
                .where(AdPayment.client_id == int(client_id), AdPayment.status == "awaiting")
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    return await confirm_payments(
        session, rows, bank=bank, now=now, summary_suffix=" — все ожидающие"
    )


__all__ = ["claim_payments", "confirm_payments", "confirm_order", "confirm_client"]
