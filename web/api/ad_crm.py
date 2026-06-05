"""CRM рекламного кабинета — блок C (`/api/ad-crm`).

Учёт заказчиков, оплат и публикаций поверх существующих заявок (`ad_requests`)
и отложенных постов (`ad_scheduled_posts`):

- ``GET    /clients``                       — список клиентов + агрегаты (оплачено, публикаций);
- ``POST   /clients``                       — завести клиента вручную;
- ``GET    /clients/{id}``                  — карточка: клиент + его оплаты + публикации;
- ``PATCH  /clients/{id}``                  — правка полей/стадии воронки;
- ``DELETE /clients/{id}``                  — удалить (оплаты каскадом, публикации → NULL);
- ``POST   /clients/upsert-from-request/{request_id}`` — завести/привязать клиента из заявки;
- ``POST   /payments`` / ``DELETE /payments/{id}``         — учёт оплат;
- ``POST   /publications`` / ``DELETE /publications/{id}`` — учёт публикаций;
- ``GET    /funnel``                        — воронка: число клиентов по стадиям + итоги.

Ключ сведения заявок предложки и ЛС в одну карточку — ``author_vk_id``.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database.connection import get_db_session
from database.models import AdClient, AdInteraction, AdPayment, AdPublication, AdRequest
from modules.ad_cabinet.interaction_log import log_interaction

logger = logging.getLogger(__name__)
router = APIRouter()

# Воронка сделки. Порядок — для сортировки/прогресса в UI.
_VALID_STAGES = ("detected", "contacted", "scheduled", "published", "paid", "lost")
_PUBLICATION_STATUSES = {"published", "removed"}
_PAYMENT_STATUSES = {"awaiting", "paid"}

# Фикс-список банков для дропдауна оплаты. Правится здесь (owner: «фикс-список»).
# Порядок — частые сверху; «Наличные»/«Перевод» как нефинансовые способы.
AD_PAYMENT_BANKS = (
    "Сбербанк",
    "Т-Банк",
    "Альфа-Банк",
    "ВТБ",
    "Озон Банк",
    "Наличные",
    "Перевод",
)


# ---------------------------------------------------------------- schemas


class ClientCreateIn(BaseModel):
    author_vk_id: int
    author_is_group: bool = False
    name: Optional[str] = None
    vk_url: Optional[str] = None
    contact: Optional[str] = None
    region_id: Optional[int] = None
    stage: str = "detected"
    notes: Optional[str] = None


class ClientUpdateIn(BaseModel):
    """Частичная правка карточки клиента (применяются только переданные поля)."""

    name: Optional[str] = None
    vk_url: Optional[str] = None
    contact: Optional[str] = None
    region_id: Optional[int] = None
    stage: Optional[str] = None
    notes: Optional[str] = None
    author_is_group: Optional[bool] = None


class PaymentCreateIn(BaseModel):
    client_id: int
    amount: float
    method: Optional[str] = None
    status: str = "paid"  # awaiting | paid
    bank: Optional[str] = None
    ad_request_id: Optional[int] = None
    scheduled_post_id: Optional[int] = None
    note: Optional[str] = None
    paid_at: Optional[str] = None  # ISO; по умолчанию — сейчас


class PaymentUpdateIn(BaseModel):
    """Частичная правка оплаты (применяются только переданные поля)."""

    amount: Optional[float] = None
    method: Optional[str] = None
    status: Optional[str] = None
    bank: Optional[str] = None
    note: Optional[str] = None
    paid_at: Optional[str] = None


class InteractionCreateIn(BaseModel):
    """Ручная заметка/событие в таймлайн клиента."""

    client_id: int
    kind: str = "note"
    summary: str
    created_at: Optional[str] = None  # ISO; по умолчанию — сейчас


class InteractionUpdateIn(BaseModel):
    """Правка события таймлайна (только переданные поля)."""

    summary: Optional[str] = None
    kind: Optional[str] = None
    created_at: Optional[str] = None


class PublicationCreateIn(BaseModel):
    community_vk_id: int
    client_id: Optional[int] = None
    vk_post_id: Optional[int] = None
    region_id: Optional[int] = None
    ad_request_id: Optional[int] = None
    scheduled_post_id: Optional[int] = None
    price: Optional[float] = None
    status: str = "published"
    note: Optional[str] = None
    published_at: Optional[str] = None  # ISO; по умолчанию — сейчас


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------- clients


@router.get("/clients")
async def list_clients(
    stage: Optional[str] = None,
    region_id: Optional[int] = None,
    q: Optional[str] = None,
    limit: int = 200,
    db: AsyncSession = Depends(get_db_session),
):
    """Список клиентов со свёрнутыми агрегатами (оплачено, число публикаций).

    Агрегаты считаются скалярными подзапросами — один проход, свежие сверху.
    """
    paid_sq = (
        select(func.coalesce(func.sum(AdPayment.amount), 0))
        .where(AdPayment.client_id == AdClient.id, AdPayment.status == "paid")
        .scalar_subquery()
    )
    awaiting_sq = (
        select(func.coalesce(func.sum(AdPayment.amount), 0))
        .where(AdPayment.client_id == AdClient.id, AdPayment.status == "awaiting")
        .scalar_subquery()
    )
    pay_count_sq = (
        select(func.count(AdPayment.id)).where(AdPayment.client_id == AdClient.id).scalar_subquery()
    )
    pub_count_sq = (
        select(func.count(AdPublication.id))
        .where(AdPublication.client_id == AdClient.id)
        .scalar_subquery()
    )

    stmt = select(
        AdClient,
        paid_sq.label("total_paid"),
        awaiting_sq.label("total_awaiting"),
        pay_count_sq.label("payments_count"),
        pub_count_sq.label("publications_count"),
    )
    if stage:
        stmt = stmt.where(AdClient.stage == stage)
    if region_id is not None:
        stmt = stmt.where(AdClient.region_id == region_id)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(AdClient.name.ilike(like) | AdClient.contact.ilike(like))
    stmt = stmt.order_by(AdClient.updated_at.desc()).limit(limit)

    rows = (await db.execute(stmt)).all()
    clients = []
    for client, total_paid, total_awaiting, payments_count, publications_count in rows:
        d = client.to_dict()
        d["total_paid"] = float(total_paid or 0)
        d["total_awaiting"] = float(total_awaiting or 0)
        d["payments_count"] = int(payments_count or 0)
        d["publications_count"] = int(publications_count or 0)
        clients.append(d)
    return {"clients": clients}


@router.post("/clients")
async def create_client(
    payload: ClientCreateIn,
    db: AsyncSession = Depends(get_db_session),
):
    """Завести клиента вручную. Дубль по ``author_vk_id`` → 409."""
    if payload.stage not in _VALID_STAGES:
        raise HTTPException(status_code=400, detail="invalid stage")

    existing = (
        await db.execute(select(AdClient).where(AdClient.author_vk_id == int(payload.author_vk_id)))
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="client already exists")

    client = AdClient(
        author_vk_id=int(payload.author_vk_id),
        author_is_group=payload.author_is_group,
        name=payload.name,
        vk_url=payload.vk_url,
        contact=payload.contact,
        region_id=payload.region_id,
        stage=payload.stage,
        notes=payload.notes,
    )
    db.add(client)
    await db.commit()
    await db.refresh(client)
    return client.to_dict()


@router.get("/clients/{client_id}")
async def get_client(
    client_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    """Карточка клиента: профиль + оплаты + публикации + сводка по деньгам."""
    client = await db.get(AdClient, client_id)
    if not client:
        raise HTTPException(status_code=404, detail="client not found")

    payments = (
        (
            await db.execute(
                select(AdPayment)
                .where(AdPayment.client_id == client_id)
                .order_by(AdPayment.paid_at.desc())
            )
        )
        .scalars()
        .all()
    )
    publications = (
        (
            await db.execute(
                select(AdPublication)
                .where(AdPublication.client_id == client_id)
                .order_by(AdPublication.published_at.desc())
            )
        )
        .scalars()
        .all()
    )

    # paid = всё, что не «awaiting» (status в БД всегда задан default'ом 'paid').
    total_paid = sum(
        float(p.amount) for p in payments if p.amount is not None and p.status != "awaiting"
    )
    total_awaiting = sum(
        float(p.amount) for p in payments if p.amount is not None and p.status == "awaiting"
    )
    return {
        "client": client.to_dict(),
        "payments": [p.to_dict() for p in payments],
        "publications": [p.to_dict() for p in publications],
        "total_paid": total_paid,
        "total_awaiting": total_awaiting,
        "publications_count": len(publications),
    }


@router.patch("/clients/{client_id}")
async def update_client(
    client_id: int,
    payload: ClientUpdateIn,
    db: AsyncSession = Depends(get_db_session),
):
    """Правка карточки: применяются только явно переданные поля."""
    client = await db.get(AdClient, client_id)
    if not client:
        raise HTTPException(status_code=404, detail="client not found")

    fields = payload.dict(exclude_unset=True)
    if "stage" in fields and fields["stage"] not in _VALID_STAGES:
        raise HTTPException(status_code=400, detail="invalid stage")
    old_stage = client.stage
    for key, value in fields.items():
        setattr(client, key, value)
    if "stage" in fields and fields["stage"] != old_stage:
        log_interaction(
            db,
            kind="status_changed",
            client_id=client.id,
            summary=f"Стадия: {old_stage} → {fields['stage']}",
            meta={"from": old_stage, "to": fields["stage"]},
        )
    await db.commit()
    await db.refresh(client)
    return client.to_dict()


@router.delete("/clients/{client_id}")
async def delete_client(
    client_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    """Удалить клиента. Оплаты уходят каскадом, публикации/заявки → client_id NULL."""
    client = await db.get(AdClient, client_id)
    if not client:
        raise HTTPException(status_code=404, detail="client not found")
    await db.delete(client)
    await db.commit()
    return {"success": True}


@router.post("/clients/upsert-from-request/{request_id}")
async def upsert_from_request(
    request_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    """Завести клиента из заявки (или привязать к существующему по ``author_vk_id``).

    Ключ — ``author_vk_id`` заявки (fallback ``peer_id``). Если клиент с таким
    VK-id уже есть — привязываем заявку к нему, не создавая дубль. Заявке
    проставляется ``client_id``.
    """
    ar = await db.get(AdRequest, request_id)
    if not ar:
        raise HTTPException(status_code=404, detail="ad request not found")

    key = ar.author_vk_id or ar.peer_id
    if not key:
        raise HTTPException(status_code=400, detail="заявка без author_vk_id/peer_id")

    existing = (
        await db.execute(select(AdClient).where(AdClient.author_vk_id == int(key)))
    ).scalar_one_or_none()

    created = False
    if existing:
        client = existing
    else:
        client = AdClient(
            author_vk_id=int(key),
            author_is_group=bool(ar.author_is_group),
            name=ar.author_name,
            region_id=ar.region_id,
            stage="contacted" if ar.status == "contacted" else "detected",
        )
        db.add(client)
        await db.flush()  # получить client.id для привязки заявки
        created = True

    ar.client_id = client.id
    # Бэкфилл: события, записанные по этой заявке до появления клиента
    # (client_id IS NULL), привязываем к клиенту — чтобы они попали в его таймлайн.
    await db.execute(
        update(AdInteraction)
        .where(
            AdInteraction.ad_request_id == request_id,
            AdInteraction.client_id.is_(None),
        )
        .values(client_id=client.id)
    )
    log_interaction(
        db,
        kind="linked" if existing else "detected",
        client_id=client.id,
        ad_request_id=request_id,
        summary=(
            "Заявка привязана к существующему клиенту" if existing else "Заведён клиент из заявки"
        ),
    )
    await db.commit()
    await db.refresh(client)
    return {"client": client.to_dict(), "created": created, "linked_request_id": request_id}


# ---------------------------------------------------------------- payments


@router.post("/payments")
async def create_payment(
    payload: PaymentCreateIn,
    db: AsyncSession = Depends(get_db_session),
):
    """Записать оплату/ожидание. ``status='paid'`` продвигает клиента в ``paid``.

    ``status='awaiting'`` — деньги ещё не пришли (согласованная ``amount``);
    клиента в воронке не двигаем (это делает фактическая оплата).
    """
    client = await db.get(AdClient, payload.client_id)
    if not client:
        raise HTTPException(status_code=404, detail="client not found")
    if payload.amount is None or float(payload.amount) <= 0:
        raise HTTPException(status_code=400, detail="amount must be positive")
    status = payload.status or "paid"
    if status not in _PAYMENT_STATUSES:
        raise HTTPException(status_code=400, detail="invalid payment status")

    now = datetime.utcnow()
    pay = AdPayment(
        client_id=int(payload.client_id),
        amount=payload.amount,
        method=payload.method,
        status=status,
        bank=payload.bank,
        ad_request_id=payload.ad_request_id,
        scheduled_post_id=payload.scheduled_post_id,
        note=payload.note,
        paid_at=_parse_dt(payload.paid_at) or now,
        paid_confirmed_at=now if status == "paid" else None,
    )
    db.add(pay)
    # В воронку «оплачено» двигаем только при фактической оплате.
    if status == "paid" and client.stage != "lost":
        client.stage = "paid"
    await db.flush()  # получить pay.id для ссылки в событии
    bank_part = f", {pay.bank}" if pay.bank else ""
    if status == "awaiting":
        summary = f"Ожидание оплаты: {float(pay.amount):g} ₽{bank_part}"
        kind = "payment_awaiting"
    else:
        summary = f"Оплата {float(pay.amount):g} ₽{bank_part}"
        kind = "payment_added"
    log_interaction(
        db,
        kind=kind,
        client_id=client.id,
        payment_id=pay.id,
        ad_request_id=payload.ad_request_id,
        scheduled_post_id=payload.scheduled_post_id,
        summary=summary,
        meta={
            "amount": float(pay.amount),
            "method": pay.method,
            "bank": pay.bank,
            "status": status,
        },
    )
    await db.commit()
    await db.refresh(pay)
    return pay.to_dict()


@router.patch("/payments/{payment_id}")
async def update_payment(
    payment_id: int,
    payload: PaymentUpdateIn,
    db: AsyncSession = Depends(get_db_session),
):
    """Правка оплаты (сумма/способ/банк/статус/дата/заметка) — всё редактируемо.

    Переход ``awaiting → paid`` ставит ``paid_confirmed_at``, двигает клиента в
    ``paid`` (если не ``lost``) и пишет событие ``payment_paid``.
    """
    pay = await db.get(AdPayment, payment_id)
    if not pay:
        raise HTTPException(status_code=404, detail="payment not found")
    fields = payload.dict(exclude_unset=True)
    if "status" in fields and fields["status"] not in _PAYMENT_STATUSES:
        raise HTTPException(status_code=400, detail="invalid payment status")
    if "amount" in fields and fields["amount"] is not None and float(fields["amount"]) <= 0:
        raise HTTPException(status_code=400, detail="amount must be positive")

    became_paid = "status" in fields and fields["status"] == "paid" and pay.status != "paid"
    for key, value in fields.items():
        if key == "paid_at":
            dt = _parse_dt(value)
            if dt is not None:
                pay.paid_at = dt
        else:
            setattr(pay, key, value)

    if became_paid:
        pay.paid_confirmed_at = datetime.utcnow()
        client = await db.get(AdClient, pay.client_id)
        if client and client.stage != "lost":
            client.stage = "paid"
        log_interaction(
            db,
            kind="payment_paid",
            client_id=pay.client_id,
            payment_id=pay.id,
            summary=f"Оплата подтверждена: {float(pay.amount):g} ₽"
            + (f", {pay.bank}" if pay.bank else ""),
            meta={"amount": float(pay.amount), "bank": pay.bank},
        )
    await db.commit()
    await db.refresh(pay)
    return pay.to_dict()


@router.delete("/payments/{payment_id}")
async def delete_payment(
    payment_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    """Удалить оплату."""
    pay = await db.get(AdPayment, payment_id)
    if not pay:
        raise HTTPException(status_code=404, detail="payment not found")
    amount = float(pay.amount) if pay.amount is not None else 0
    log_interaction(
        db,
        kind="payment_deleted",
        client_id=pay.client_id,
        summary=f"Удалена оплата {amount:g} ₽",
        meta={"amount": amount},
    )
    await db.delete(pay)
    await db.commit()
    return {"success": True}


@router.get("/banks")
async def list_banks(db: AsyncSession = Depends(get_db_session)):
    """Банки для дропдауна оплаты + частоты использования (куда чаще платят).

    Возвращает фикс-список ``AD_PAYMENT_BANKS`` и счётчики по фактическим оплатам
    (status='paid'), отсортированные по убыванию — чтобы видеть предпочитаемый банк.
    """
    rows = (
        await db.execute(
            select(
                AdPayment.bank,
                func.count(AdPayment.id),
                func.coalesce(func.sum(AdPayment.amount), 0),
            )
            .where(AdPayment.status == "paid", AdPayment.bank.isnot(None))
            .group_by(AdPayment.bank)
            .order_by(func.count(AdPayment.id).desc())
        )
    ).all()
    stats = [
        {"bank": bank, "count": int(cnt or 0), "total": float(total or 0)}
        for bank, cnt, total in rows
    ]
    return {"banks": list(AD_PAYMENT_BANKS), "stats": stats}


# ---------------------------------------------------------------- publications


@router.post("/publications")
async def create_publication(
    payload: PublicationCreateIn,
    db: AsyncSession = Depends(get_db_session),
):
    """Записать публикацию. Привязанного клиента продвигаем в ``published``."""
    if payload.status not in _PUBLICATION_STATUSES:
        raise HTTPException(status_code=400, detail="invalid status")

    client = None
    if payload.client_id is not None:
        client = await db.get(AdClient, payload.client_id)
        if not client:
            raise HTTPException(status_code=404, detail="client not found")

    pub = AdPublication(
        client_id=payload.client_id,
        community_vk_id=int(payload.community_vk_id),
        vk_post_id=payload.vk_post_id,
        region_id=payload.region_id,
        ad_request_id=payload.ad_request_id,
        scheduled_post_id=payload.scheduled_post_id,
        price=payload.price,
        status=payload.status,
        note=payload.note,
        published_at=_parse_dt(payload.published_at) or datetime.utcnow(),
    )
    db.add(pub)
    # Не понижаем стадию уже оплаченного клиента — published только вперёд по воронке.
    if client is not None and client.stage in ("detected", "contacted", "scheduled"):
        client.stage = "published"
    await db.flush()  # получить pub.id для ссылки в событии
    log_interaction(
        db,
        kind="published",
        client_id=payload.client_id,
        publication_id=pub.id,
        ad_request_id=payload.ad_request_id,
        scheduled_post_id=payload.scheduled_post_id,
        summary=f"Публикация в сообщество {payload.community_vk_id}"
        + (f" ({payload.price:g} ₽)" if payload.price else ""),
        meta={"community_vk_id": int(payload.community_vk_id), "price": payload.price},
    )
    await db.commit()
    await db.refresh(pub)
    return pub.to_dict()


@router.delete("/publications/{publication_id}")
async def delete_publication(
    publication_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    """Удалить запись о публикации."""
    pub = await db.get(AdPublication, publication_id)
    if not pub:
        raise HTTPException(status_code=404, detail="publication not found")
    log_interaction(
        db,
        kind="publication_deleted",
        client_id=pub.client_id,
        summary=f"Удалена запись о публикации в сообщество {pub.community_vk_id}",
    )
    await db.delete(pub)
    await db.commit()
    return {"success": True}


# ---------------------------------------------------------------- funnel


# ---------------------------------------------------------------- timeline


@router.get("/clients/{client_id}/timeline")
async def client_timeline(
    client_id: int,
    limit: int = 200,
    db: AsyncSession = Depends(get_db_session),
):
    """Хронология событий клиента (свежие сверху) — из ``ad_interactions``."""
    client = await db.get(AdClient, client_id)
    if not client:
        raise HTTPException(status_code=404, detail="client not found")
    rows = (
        (
            await db.execute(
                select(AdInteraction)
                .where(AdInteraction.client_id == client_id)
                .order_by(AdInteraction.created_at.desc(), AdInteraction.id.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return {"timeline": [r.to_dict() for r in rows]}


@router.post("/interactions")
async def create_interaction(
    payload: InteractionCreateIn,
    db: AsyncSession = Depends(get_db_session),
):
    """Добавить ручную заметку/событие в таймлайн клиента."""
    client = await db.get(AdClient, payload.client_id)
    if not client:
        raise HTTPException(status_code=404, detail="client not found")
    if not (payload.summary or "").strip():
        raise HTTPException(status_code=400, detail="summary required")
    rec = log_interaction(
        db,
        kind=(payload.kind or "note").strip(),
        client_id=payload.client_id,
        summary=payload.summary.strip(),
    )
    dt = _parse_dt(payload.created_at)
    if dt is not None:
        rec.created_at = dt
    await db.commit()
    await db.refresh(rec)
    return rec.to_dict()


@router.patch("/interactions/{interaction_id}")
async def update_interaction(
    interaction_id: int,
    payload: InteractionUpdateIn,
    db: AsyncSession = Depends(get_db_session),
):
    """Правка события таймлайна (текст/вид/время) — для исправления опечаток."""
    rec = await db.get(AdInteraction, interaction_id)
    if not rec:
        raise HTTPException(status_code=404, detail="interaction not found")
    fields = payload.dict(exclude_unset=True)
    if "summary" in fields and fields["summary"] is not None:
        rec.summary = fields["summary"]
    if "kind" in fields and fields["kind"]:
        rec.kind = fields["kind"]
    if "created_at" in fields:
        dt = _parse_dt(fields["created_at"])
        if dt is not None:
            rec.created_at = dt
    await db.commit()
    await db.refresh(rec)
    return rec.to_dict()


@router.delete("/interactions/{interaction_id}")
async def delete_interaction(
    interaction_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    """Удалить ошибочное событие таймлайна."""
    rec = await db.get(AdInteraction, interaction_id)
    if not rec:
        raise HTTPException(status_code=404, detail="interaction not found")
    await db.delete(rec)
    await db.commit()
    return {"success": True}


# ---------------------------------------------------------------- funnel


@router.get("/funnel")
async def funnel(db: AsyncSession = Depends(get_db_session)):
    """Сводка воронки: число клиентов по стадиям + деньги/публикации итого."""
    stage_rows = (
        await db.execute(select(AdClient.stage, func.count(AdClient.id)).group_by(AdClient.stage))
    ).all()
    by_stage = {stage: 0 for stage in _VALID_STAGES}
    total_clients = 0
    for stage, count in stage_rows:
        by_stage[stage] = int(count or 0)
        total_clients += int(count or 0)

    total_paid = (
        await db.execute(
            select(func.coalesce(func.sum(AdPayment.amount), 0)).where(AdPayment.status == "paid")
        )
    ).scalar_one()
    total_awaiting = (
        await db.execute(
            select(func.coalesce(func.sum(AdPayment.amount), 0)).where(
                AdPayment.status == "awaiting"
            )
        )
    ).scalar_one()
    publications_count = (await db.execute(select(func.count(AdPublication.id)))).scalar_one()

    return {
        "stages": _VALID_STAGES,
        "by_stage": by_stage,
        "total_clients": total_clients,
        "total_paid": float(total_paid or 0),
        "total_awaiting": float(total_awaiting or 0),
        "publications_count": int(publications_count or 0),
    }
