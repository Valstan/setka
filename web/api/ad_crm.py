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
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database.connection import get_db_session
from database.models import (
    AdClient,
    AdInteraction,
    AdOrderItem,
    AdPayment,
    AdPublication,
    AdRequest,
)
from modules.ad_cabinet.interaction_log import log_interaction
from utils.search_query import compact_number, normalize_query, query_variants

logger = logging.getLogger(__name__)

# Порог pg_trgm similarity для fuzzy-фолбэка поиска клиентов (#035, Уровень 3).
_FUZZY_SIMILARITY_THRESHOLD = 0.3
router = APIRouter()

# Воронка сделки. Порядок — для сортировки/прогресса в UI.
_VALID_STAGES = ("detected", "contacted", "scheduled", "published", "paid", "lost")
_PUBLICATION_STATUSES = {"published", "removed"}
_PAYMENT_STATUSES = {"awaiting", "paid"}
_ORDER_ITEM_STATUSES = {"planned", "scheduled", "published", "cancelled"}

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


class OrderItemCreateIn(BaseModel):
    """Позиция заказа клиента (что/сколько/период). Вручную или из заявки."""

    client_id: int
    description: Optional[str] = None
    quantity: int = 1
    period_start: Optional[str] = None  # ISO date
    period_end: Optional[str] = None
    status: str = "planned"
    ad_request_id: Optional[int] = None
    scheduled_post_id: Optional[int] = None
    publication_id: Optional[int] = None
    note: Optional[str] = None


class OrderItemUpdateIn(BaseModel):
    """Частичная правка позиции заказа (только переданные поля)."""

    description: Optional[str] = None
    quantity: Optional[int] = None
    period_start: Optional[str] = None
    period_end: Optional[str] = None
    status: Optional[str] = None
    note: Optional[str] = None


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def _parse_date(value: Optional[str]):
    """ISO-дата (YYYY-MM-DD) → date | None. Терпимо к пустым/битым строкам."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).date()
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------- clients


def _compact_contact_col():
    """``contact`` без разделителей — матч «номерных» токенов (``8912…`` ≡ ``8-912 …``)."""
    col = func.coalesce(AdClient.contact, "")
    for sep in (" ", "-", ".", "/"):
        col = func.replace(col, sep, "")
    return col


def _text_search_conditions(tokens: list[str]) -> list:
    """Многотокен AND (#035, Уровень 1): каждый токен — substring в name/contact."""
    conds = []
    compact_col = _compact_contact_col()
    for token in tokens:
        like = f"%{token}%"
        cond = AdClient.name.ilike(like) | AdClient.contact.ilike(like)
        compact = compact_number(token)
        if compact:
            cond = cond | compact_col.like(f"%{compact}%")
        conds.append(cond)
    return conds


def _supports_trgm(db: AsyncSession) -> bool:
    """pg_trgm есть только на Postgres; на других диалектах fuzzy пропускаем."""
    try:
        return db.get_bind().dialect.name == "postgresql"
    except Exception:
        return False


async def _search_client_rows(db: AsyncSession, stmt, q: Optional[str], limit: int):
    """Tiered-поиск клиентов (#035): substring → RU↔EN раскладка → pg_trgm fuzzy.

    Следующий уровень пробуется только при нуле результатов предыдущего —
    «похожее» не разбавляет точные совпадения.
    """

    def _ordered(s):
        return s.order_by(AdClient.updated_at.desc()).limit(limit)

    variants = query_variants(q) if q else []
    if not variants:
        return (await db.execute(_ordered(stmt))).all()

    for tokens in variants:
        rows = (await db.execute(_ordered(stmt.where(*_text_search_conditions(tokens))))).all()
        if rows:
            return rows

    if not _supports_trgm(db):
        return []

    qn = normalize_query(q)
    sim = func.greatest(
        func.similarity(AdClient.name, qn),
        func.similarity(func.coalesce(AdClient.contact, ""), qn),
    )
    fuzzy_stmt = stmt.where(sim > _FUZZY_SIMILARITY_THRESHOLD).order_by(sim.desc()).limit(limit)
    return (await db.execute(fuzzy_stmt)).all()


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

    rows = await _search_client_rows(db, stmt, q, limit)
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


# ---------------------------------------------------------------- client chat (PR-5)


class ClientReplyIn(BaseModel):
    """Ответ клиенту из чата карточки."""

    message: str


async def _resolve_client_dialog(db: AsyncSession, client_id: int):
    """(community_vk_id, peer_id) для чата клиента — из его свежей заявки с peer.

    Берём последнюю заявку клиента с положительным ``peer_id`` (предпочитая ЛС,
    но и у предложки peer есть). Возвращает (None, None), если диалога нет.
    """
    ar = (
        (
            await db.execute(
                select(AdRequest)
                .where(
                    AdRequest.client_id == client_id,
                    AdRequest.peer_id.isnot(None),
                    AdRequest.peer_id > 0,
                )
                .order_by(
                    AdRequest.origin.desc(), AdRequest.detected_at.desc(), AdRequest.id.desc()
                )
            )
        )
        .scalars()
        .first()
    )
    if not ar:
        return None, None, None
    return int(ar.community_vk_id), int(ar.peer_id), ar


@router.get("/clients/{client_id}/thread")
async def client_thread(
    client_id: int,
    count: int = 30,
    db: AsyncSession = Depends(get_db_session),
):
    """Двусторонняя переписка с клиентом (вход + наши ответы) — для чата в карточке.

    ``messages.getHistory`` уже содержит и входящие, и наши исходящие (``out``),
    поэтому видно «я ему уже отвечал». Никогда не 500-ит на флапе VK.
    """
    import asyncio

    from modules.notifications.vk_dialogs_checker import VKDialogsChecker
    from modules.vk_token_router import load_vk_routing

    client = await db.get(AdClient, client_id)
    if not client:
        raise HTTPException(status_code=404, detail="client not found")

    community_vk_id, peer_id, _ = await _resolve_client_dialog(db, client_id)
    if not peer_id:
        return {"messages": [], "reason": "no_dialog"}

    user_token, community_tokens = await load_vk_routing()
    if not user_token:
        return {"messages": [], "reason": "no_token"}

    try:
        checker = VKDialogsChecker(user_token, community_tokens=community_tokens)
        messages = await asyncio.to_thread(checker.fetch_history, community_vk_id, peer_id, count)
    except Exception as e:  # pragma: no cover - защита от неожиданного
        logger.warning("client thread fetch failed for %s: %s", client_id, e)
        return {"messages": [], "reason": "error", "error": str(e)}

    dialog_url = f"https://vk.com/gim{abs(community_vk_id)}?sel={peer_id}"
    return {
        "messages": messages,
        "community_vk_id": community_vk_id,
        "peer_id": peer_id,
        "dialog_url": dialog_url,
    }


@router.post("/clients/{client_id}/reply")
async def client_reply(
    client_id: int,
    payload: ClientReplyIn,
    db: AsyncSession = Depends(get_db_session),
):
    """Отправить ответ клиенту от имени сообщества (полу-авто, как в кабинете).

    Цель (community, peer) — из свежей заявки клиента. VK 901/нет доступа →
    возвращаем ``personal_deeplink`` (оператор пишет с личного). Успех пишется в
    таймлайн (kind=reply_sent), чтобы видеть «я ему уже отвечал».
    """
    from modules.notifications.vk_actions import send_message
    from modules.vk_token_router import load_vk_routing

    client = await db.get(AdClient, client_id)
    if not client:
        raise HTTPException(status_code=404, detail="client not found")
    if not (payload.message or "").strip():
        raise HTTPException(status_code=400, detail="empty message")

    community_vk_id, peer_id, _ = await _resolve_client_dialog(db, client_id)
    if not peer_id:
        raise HTTPException(status_code=400, detail="нет диалога с клиентом")

    user_token, community_tokens = await load_vk_routing()
    if not user_token:
        return {"success": False, "error": "VK token not found"}

    res = send_message(
        group_id=community_vk_id,
        peer_id=peer_id,
        message=payload.message.strip(),
        user_token=user_token,
        community_tokens=community_tokens,
        random_id=0,
    )

    if res.get("success"):
        log_interaction(
            db,
            kind="reply_sent",
            client_id=client_id,
            summary="Ответ клиенту (чат): " + payload.message.strip()[:120],
            meta={"via": res.get("via")},
        )
        await db.commit()
        return {"success": True, "via": res.get("via")}

    if res.get("allowed") is False:
        return {
            "allowed": False,
            "personal_deeplink": res.get("personal_deeplink") or f"https://vk.com/im?sel={peer_id}",
            "error_code": res.get("error_code"),
        }
    return {"success": False, "error": res.get("error"), "error_code": res.get("error_code")}


# ---------------------------------------------------------------- stats (С3)


@router.post("/clients/{client_id}/refresh-stats")
async def refresh_client_stats(
    client_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    """Обновить метрики (просмотры/лайки/репосты) публикаций клиента сейчас (С3).

    Кнопка «Обновить» в карточке. Тянет свежие цифры из VK для публикаций этого
    клиента; UI затем перечитывает карточку. Фон раз в день делает то же по всем.
    """
    client = await db.get(AdClient, client_id)
    if not client:
        raise HTTPException(status_code=404, detail="client not found")

    from modules.ad_cabinet.publication_stats import run_collect_stats

    result = await run_collect_stats(only_client_id=client_id)
    return result


@router.get("/clients/{client_id}/stats-report")
async def client_stats_report(
    client_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    """Готовый текст отчёта клиенту по метрикам его размещений (С3).

    Собирается из уже сохранённых метрик публикаций (status='published'). UI
    предзаполняет этим текстом чат — оператор правит и отправляет клиенту.
    """
    client = await db.get(AdClient, client_id)
    if not client:
        raise HTTPException(status_code=404, detail="client not found")

    pubs = (
        (
            await db.execute(
                select(AdPublication)
                .where(
                    AdPublication.client_id == client_id,
                    AdPublication.status == "published",
                )
                .order_by(AdPublication.published_at.desc())
            )
        )
        .scalars()
        .all()
    )
    measured = [p for p in pubs if p.stats_updated_at is not None]
    total_views = sum(int(p.views or 0) for p in measured)

    if not measured:
        report = "Статистика по вашим размещениям ещё собирается — заглянем чуть позже."
    else:
        lines = []
        for p in measured:
            when = p.published_at.strftime("%d.%m.%Y") if p.published_at else ""
            url = p.to_dict().get("vk_post_url") or f"сообщество {p.community_vk_id}"
            lines.append(
                f"• {when}: 👁 {int(p.views or 0)} просмотров, "
                f"❤ {int(p.likes or 0)}, 🔁 {int(p.reposts or 0)}\n  {url}"
            )
        report = (
            "📊 Статистика ваших рекламных размещений:\n\n"
            + "\n".join(lines)
            + f"\n\nИтого просмотров: {total_views}."
        )
    return {
        "report": report,
        "publications_measured": len(measured),
        "total_views": total_views,
    }


# ---------------------------------------------------------------- order items


@router.get("/clients/{client_id}/order-items")
async def list_order_items(
    client_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    """Позиции заказа клиента (что и сколько реклам заказано/будет опубликовано)."""
    client = await db.get(AdClient, client_id)
    if not client:
        raise HTTPException(status_code=404, detail="client not found")
    rows = (
        (
            await db.execute(
                select(AdOrderItem)
                .where(AdOrderItem.client_id == client_id)
                .order_by(AdOrderItem.created_at.desc(), AdOrderItem.id.desc())
            )
        )
        .scalars()
        .all()
    )
    total_qty = sum(int(r.quantity or 0) for r in rows)
    return {"order_items": [r.to_dict() for r in rows], "total_quantity": total_qty}


@router.post("/order-items")
async def create_order_item(
    payload: OrderItemCreateIn,
    db: AsyncSession = Depends(get_db_session),
):
    """Добавить позицию заказа вручную."""
    client = await db.get(AdClient, payload.client_id)
    if not client:
        raise HTTPException(status_code=404, detail="client not found")
    if payload.status not in _ORDER_ITEM_STATUSES:
        raise HTTPException(status_code=400, detail="invalid order item status")
    if payload.quantity is not None and int(payload.quantity) < 1:
        raise HTTPException(status_code=400, detail="quantity must be >= 1")

    item = AdOrderItem(
        client_id=int(payload.client_id),
        description=payload.description,
        quantity=int(payload.quantity or 1),
        period_start=_parse_date(payload.period_start),
        period_end=_parse_date(payload.period_end),
        status=payload.status,
        ad_request_id=payload.ad_request_id,
        scheduled_post_id=payload.scheduled_post_id,
        publication_id=payload.publication_id,
        note=payload.note,
    )
    db.add(item)
    log_interaction(
        db,
        kind="order_item",
        client_id=client.id,
        ad_request_id=payload.ad_request_id,
        summary="Позиция заказа: "
        + ((payload.description or "без описания")[:80])
        + f" ×{item.quantity}",
    )
    await db.commit()
    await db.refresh(item)
    return item.to_dict()


@router.post("/order-items/from-request/{request_id}")
async def create_order_item_from_request(
    request_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    """Подтянуть позицию заказа из заявки предложки/ЛС (описание из текста заявки).

    Заявка должна быть привязана к клиенту (``client_id``) — иначе 400 с просьбой
    сначала завести/привязать клиента (кнопка «В CRM» на карточке заявки).
    """
    ar = await db.get(AdRequest, request_id)
    if not ar:
        raise HTTPException(status_code=404, detail="ad request not found")
    if not ar.client_id:
        raise HTTPException(status_code=400, detail="заявка не привязана к клиенту")

    description = (ar.text_snapshot or ar.community_name or "").strip()[:500]
    item = AdOrderItem(
        client_id=int(ar.client_id),
        ad_request_id=request_id,
        description=description or "реклама из заявки",
        quantity=1,
        status="planned",
    )
    db.add(item)
    log_interaction(
        db,
        kind="order_item",
        client_id=int(ar.client_id),
        ad_request_id=request_id,
        summary="Позиция заказа из заявки: " + (description[:80] or "реклама"),
    )
    await db.commit()
    await db.refresh(item)
    return item.to_dict()


@router.patch("/order-items/{item_id}")
async def update_order_item(
    item_id: int,
    payload: OrderItemUpdateIn,
    db: AsyncSession = Depends(get_db_session),
):
    """Правка позиции заказа (описание/кол-во/период/статус/заметка)."""
    item = await db.get(AdOrderItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="order item not found")
    fields = payload.dict(exclude_unset=True)
    if "status" in fields and fields["status"] not in _ORDER_ITEM_STATUSES:
        raise HTTPException(status_code=400, detail="invalid order item status")
    if "quantity" in fields and fields["quantity"] is not None and int(fields["quantity"]) < 1:
        raise HTTPException(status_code=400, detail="quantity must be >= 1")
    for key, value in fields.items():
        if key in ("period_start", "period_end"):
            setattr(item, key, _parse_date(value))
        else:
            setattr(item, key, value)
    await db.commit()
    await db.refresh(item)
    return item.to_dict()


@router.delete("/order-items/{item_id}")
async def delete_order_item(
    item_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    """Удалить позицию заказа."""
    item = await db.get(AdOrderItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="order item not found")
    await db.delete(item)
    await db.commit()
    return {"success": True}


# ---------------------------------------------------------------- stats / charts


@router.get("/stats/timeseries")
async def stats_timeseries(
    days: int = 30,
    db: AsyncSession = Depends(get_db_session),
):
    """Динамика по дням за ``days`` дней: рост предложений и оплаченной рекламы.

    Возвращает непрерывные ряды (дни без данных = 0) для Chart.js:
      * ``offers`` — число заявок (``ad_requests``) по ``detected_at``;
      * ``paid``   — число и сумма оплат (``status='paid'``) по ``paid_at``.
    """
    days = max(1, min(int(days), 365))
    start = (datetime.utcnow() - timedelta(days=days - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    offer_rows = (
        await db.execute(
            select(func.date(AdRequest.detected_at), func.count(AdRequest.id))
            .where(AdRequest.detected_at >= start)
            .group_by(func.date(AdRequest.detected_at))
        )
    ).all()
    paid_rows = (
        await db.execute(
            select(
                func.date(AdPayment.paid_at),
                func.count(AdPayment.id),
                func.coalesce(func.sum(AdPayment.amount), 0),
            )
            .where(AdPayment.status == "paid", AdPayment.paid_at >= start)
            .group_by(func.date(AdPayment.paid_at))
        )
    ).all()

    def _key(d) -> str:
        return d.isoformat() if hasattr(d, "isoformat") else str(d)

    offers_by_day = {_key(d): int(c or 0) for d, c in offer_rows}
    paid_by_day = {_key(d): (int(c or 0), float(s or 0)) for d, c, s in paid_rows}

    labels, offers, paid_count, paid_amount = [], [], [], []
    for i in range(days):
        day = (start + timedelta(days=i)).date().isoformat()
        labels.append(day)
        offers.append(offers_by_day.get(day, 0))
        c, s = paid_by_day.get(day, (0, 0.0))
        paid_count.append(c)
        paid_amount.append(s)

    return {
        "days": days,
        "labels": labels,
        "offers": offers,
        "paid_count": paid_count,
        "paid_amount": paid_amount,
    }


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
    total_views = (
        await db.execute(select(func.coalesce(func.sum(AdPublication.views), 0)))
    ).scalar_one()

    return {
        "stages": _VALID_STAGES,
        "by_stage": by_stage,
        "total_clients": total_clients,
        "total_paid": float(total_paid or 0),
        "total_awaiting": float(total_awaiting or 0),
        "publications_count": int(publications_count or 0),
        "total_views": int(total_views or 0),
    }
