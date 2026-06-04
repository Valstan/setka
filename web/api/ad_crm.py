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
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.connection import get_db_session
from database.models import AdClient, AdPayment, AdPublication, AdRequest

logger = logging.getLogger(__name__)
router = APIRouter()

# Воронка сделки. Порядок — для сортировки/прогресса в UI.
_VALID_STAGES = ("detected", "contacted", "scheduled", "published", "paid", "lost")
_PUBLICATION_STATUSES = {"published", "removed"}


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
    ad_request_id: Optional[int] = None
    scheduled_post_id: Optional[int] = None
    note: Optional[str] = None
    paid_at: Optional[str] = None  # ISO; по умолчанию — сейчас


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
        .where(AdPayment.client_id == AdClient.id)
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
    for client, total_paid, payments_count, publications_count in rows:
        d = client.to_dict()
        d["total_paid"] = float(total_paid or 0)
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

    total_paid = sum(float(p.amount) for p in payments if p.amount is not None)
    return {
        "client": client.to_dict(),
        "payments": [p.to_dict() for p in payments],
        "publications": [p.to_dict() for p in publications],
        "total_paid": total_paid,
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
    for key, value in fields.items():
        setattr(client, key, value)
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
    await db.commit()
    await db.refresh(client)
    return {"client": client.to_dict(), "created": created, "linked_request_id": request_id}


# ---------------------------------------------------------------- payments


@router.post("/payments")
async def create_payment(
    payload: PaymentCreateIn,
    db: AsyncSession = Depends(get_db_session),
):
    """Записать оплату. Клиента продвигаем в ``paid`` (если он не ``lost``)."""
    client = await db.get(AdClient, payload.client_id)
    if not client:
        raise HTTPException(status_code=404, detail="client not found")
    if payload.amount is None or float(payload.amount) <= 0:
        raise HTTPException(status_code=400, detail="amount must be positive")

    pay = AdPayment(
        client_id=int(payload.client_id),
        amount=payload.amount,
        method=payload.method,
        ad_request_id=payload.ad_request_id,
        scheduled_post_id=payload.scheduled_post_id,
        note=payload.note,
        paid_at=_parse_dt(payload.paid_at) or datetime.utcnow(),
    )
    db.add(pay)
    if client.stage != "lost":
        client.stage = "paid"
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
    await db.delete(pay)
    await db.commit()
    return {"success": True}


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
    await db.delete(pub)
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
        await db.execute(select(func.coalesce(func.sum(AdPayment.amount), 0)))
    ).scalar_one()
    publications_count = (await db.execute(select(func.count(AdPublication.id)))).scalar_one()

    return {
        "stages": _VALID_STAGES,
        "by_stage": by_stage,
        "total_clients": total_clients,
        "total_paid": float(total_paid or 0),
        "publications_count": int(publications_count or 0),
    }
