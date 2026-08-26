"""API кабинета рекламодателя (`/api/advertiser`) — клиентская половина ad-CRM.

Клиент входит через ЕСА (роль ``advertiser``, зона гейта ``ADVERTISER_PREFIXES``)
и видит ТОЛЬКО СВОЁ: каждый хендлер начинается с ``_current_client()`` — карточка
резолвится из сессии (``modules/ad_cabinet/advertiser_link``), ``client_id`` из
тела запроса не читается нигде. Цену считает только сервер (``quote_price``).

Публикация — существующим циклом операторского планировщика
(``modules/ad_cabinet/client_orders``): N строк ``AdScheduledPost`` → VK-отложка;
фиксацию выхода и ``AdPayment(awaiting)`` делает ``publish_reconciler``. Клиент
своих платежей не создаёт; «оплачено» подтверждает владелец в CRM.

Фото клиента живут в ``web/uploads/advertiser/<client_id>/`` — СОЗНАТЕЛЬНО вне
``/static`` (тот примонтирован публично): отдача только владельцу карточки.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config.ad_landing import PAYMENTS, build_price_table, quote_price
from database.connection import get_db_session
from database.models import AdPayment, AdPublication, AdScheduledPost, Region
from modules.ad_cabinet import advertiser_link, chat, client_orders
from modules.ad_cabinet.balance import compute_balance
from modules.ad_cabinet.interaction_log import log_interaction

logger = logging.getLogger(__name__)
router = APIRouter()

# Лимиты фото: расширения/вес — те же, что у операторских офферных картинок.
from web.api.ad_cabinet import _ALLOWED_IMG_EXT, _MAX_IMG_BYTES, _msk_to_unix  # noqa: E402

MAX_PHOTOS_PER_POST = 10
MAX_PHOTOS_PER_CLIENT = 20


def _current_user(request: Request):
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


async def _current_client(request: Request, db: AsyncSession):
    """Карточка клиента текущей сессии. 403 — юзер ещё не рекламодатель."""
    user = _current_user(request)
    client = await advertiser_link.resolve_client(db, user)
    if client is None:
        raise HTTPException(status_code=403, detail="Не рекламодатель — пройдите онбординг")
    return user, client


# ----------------------------------------------------------------------
# Онбординг и профиль
# ----------------------------------------------------------------------


class OnboardingIn(BaseModel):
    name: Optional[str] = Field(None, max_length=300)
    phone: Optional[str] = Field(None, max_length=50)


@router.get("/me")
async def me(request: Request, db: AsyncSession = Depends(get_db_session)):
    """Статус текущего юзера в кабинете. Доступен ЛЮБОМУ аутентифицированному
    (гейт: ``ADVERTISER_ONBOARDING_EXACT``) — не-рекламодателю отвечает
    ``is_advertiser=False``, страница показывает онбординг."""
    user = _current_user(request)
    client = await advertiser_link.resolve_client(db, user)
    if client is not None:
        await db.commit()  # fallback-линковка могла записать FK (self-healing)
    return {
        "is_advertiser": client is not None,
        "role": getattr(user, "role", None),
        "display_name": getattr(user, "display_name", None) or getattr(user, "login", None),
        "client": client.to_dict() if client is not None else None,
    }


@router.post("/onboarding")
async def onboarding(
    payload: OnboardingIn, request: Request, db: AsyncSession = Depends(get_db_session)
):
    """Стать рекламодателем: линковка/создание карточки + роль ``advertiser``.

    Доступен любому аутентифицированному (гейт), идемпотентен: повторный вызов
    возвращает уже привязанную карточку.
    """
    user = _current_user(request)
    was_new = await advertiser_link.resolve_client(db, user) is None
    client = await advertiser_link.onboard_client(db, user, name=payload.name, phone=payload.phone)
    if getattr(user, "role", None) not in ("advertiser", "operator"):
        # Роль читается из БД на каждом запросе — вступает в силу сразу,
        # без перевыпуска сессионной куки.
        user.role = "advertiser"
        db.add(user)
    if was_new:
        await db.flush()  # id новой карточки нужен журналу до commit
        log_interaction(
            db,
            kind="cabinet_signup",
            client_id=client.id,
            summary=f"Новый клиент в кабинете: {client.name or getattr(user, 'login', '?')}",
            actor="client",
        )
    await db.commit()
    await db.refresh(client)
    if was_new:
        import asyncio

        from modules.ad_cabinet import owner_ping

        # Глобальный бюджет пинга: регистрация публична, и скриптовые аккаунты
        # не должны превращать Telegram владельца в ленту «новых клиентов»
        # (should-fix ревью). Событие в таймлайне пишется всегда — бюджет
        # только на пинг.
        if await asyncio.to_thread(owner_ping.event_budget_pass, "signup_ping", limit=5, ttl=3600):
            await asyncio.to_thread(
                owner_ping.notify_owner,
                f"🆕 Кабинет: новый клиент «{_telemetry_identity(user, client)}» "
                "прошёл онбординг — карточка в /ad → Кабинеты",
            )
    return {"is_advertiser": True, "client": client.to_dict()}


class TelemetryIn(BaseModel):
    """Событие из браузера клиента: визит либо JS-ошибка.

    Пишется в тот же таймлайн ``ad_interactions`` — владелец видит, что клиенты
    ПРИХОДЯТ и что у них ЛОМАЕТСЯ, не дожидаясь жалобы (заказ владельца
    2026-08-26: «клиенты заходили, ничего не работало, а я не знал»).
    """

    kind: str = Field(..., pattern=r"^(visit|js_error)$")
    message: str = Field("", max_length=500)
    source: str = Field("", max_length=300)


def _telemetry_identity(user, client) -> str:
    """Имя для сводной ленты: ВК-аккаунт без login не должен светиться «None»."""
    return (
        (client.name if client is not None else None)
        or getattr(user, "display_name", None)
        or getattr(user, "login", None)
        or f"user#{getattr(user, 'id', '?')}"
    )


# Потолок записей js_error на аккаунт в час — контент-независимый (ротация
# текста не выбивает новые вёдра, блокер ревью 2026-08-26).
JS_ERROR_BUDGET_PER_HOUR = 10


@router.post("/telemetry")
async def telemetry(
    payload: TelemetryIn, request: Request, db: AsyncSession = Depends(get_db_session)
):
    """Принять событие браузера клиента (доступен ЛЮБОМУ аутентифицированному —
    ошибка на странице онбординга ценнее прочих: до неё клиент ещё «никто»).

    Анти-шум: ``visit`` пишется не чаще раза в час на аккаунт; ``js_error`` —
    бюджет 10/час на аккаунт + дедуп одинакового текста на 10 минут; пинг
    владельцу — не чаще раза в час на аккаунт. Троттлы зовутся в thread'е
    (синхронный Redis не блокирует event loop). Ответ фиксированный — состояние
    серверных окон клиенту не раскрывается.
    """
    import asyncio

    user = _current_user(request)
    client = await advertiser_link.resolve_client(db, user)
    client_id = client.id if client is not None else None
    who = _telemetry_identity(user, client)

    from modules.ad_cabinet import owner_ping

    logged = False
    ping_text = None
    if payload.kind == "visit":
        if await asyncio.to_thread(owner_ping.ping_dedup_pass, f"visit:{user.id}", ttl=3600):
            log_interaction(
                db,
                kind="cabinet_visit",
                client_id=client_id,
                summary=f"Клиент открыл кабинет: {who}",
                actor="client",
            )
            logged = True
    else:  # js_error
        detail = " ".join(x for x in (payload.message.strip(), payload.source.strip()) if x)
        budget_ok = await asyncio.to_thread(
            owner_ping.event_budget_pass,
            f"js_error:{user.id}",
            limit=JS_ERROR_BUDGET_PER_HOUR,
            ttl=3600,
        )
        fresh = budget_ok and await asyncio.to_thread(
            owner_ping.ping_dedup_pass,
            f"js_error_log:{user.id}:{owner_ping.stable_digest(detail[:200])}",
            ttl=600,
        )
        if fresh:
            log_interaction(
                db,
                kind="cabinet_js_error",
                client_id=client_id,
                summary=f"Ошибка в браузере клиента {who}: {detail[:400]}",
                meta={"message": payload.message, "source": payload.source},
                actor="client",
            )
            logged = True
        ping_text = f"⚠️ Кабинет: у клиента «{who}» ошибка в браузере — {detail[:200]}"
    if logged:
        await db.commit()
    if ping_text:
        # После commit: висящий Telegram не держит открытую транзакцию.
        await asyncio.to_thread(owner_ping.notify_owner, ping_text, dedup_key=f"js_error:{user.id}")
    return {"ok": True}


@router.get("/summary")
async def summary(request: Request, db: AsyncSession = Depends(get_db_session)):
    """Баланс клиента — тем же ``compute_balance``, что у оператора (единый
    источник «сколько должен»), плюс производная «запланировано на сумму»."""
    _user, client = await _current_client(request, db)
    payments = (
        (await db.execute(select(AdPayment).where(AdPayment.client_id == client.id)))
        .scalars()
        .all()
    )
    publications = (
        (await db.execute(select(AdPublication).where(AdPublication.client_id == client.id)))
        .scalars()
        .all()
    )
    scheduled = (
        (
            await db.execute(
                select(AdScheduledPost).where(
                    AdScheduledPost.client_id == client.id,
                    AdScheduledPost.status.in_(client_orders.ACTIVE_STATUSES),
                )
            )
        )
        .scalars()
        .all()
    )
    planned = sum(float(p.price) for p in scheduled if p.price is not None)
    unread = await chat.unread_count(db, client.id, reader=chat.SENDER_CLIENT)
    return {
        "client": client.to_dict(),
        "balance": compute_balance(payments, publications),
        "planned_total": round(planned, 2),
        "planned_posts": len(scheduled),
        "chat_unread": unread,
    }


# ----------------------------------------------------------------------
# Прайс и районы
# ----------------------------------------------------------------------


@router.get("/price-table")
async def price_table(request: Request, db: AsyncSession = Depends(get_db_session)):
    """Прайс + районы, доступные для размещения (активные с группой)."""
    await _current_client(request, db)
    rows = (
        await db.execute(
            select(Region.id, Region.name, Region.center_city)
            .where(Region.is_active.is_(True), Region.vk_group_id.isnot(None))
            .order_by(Region.name.asc())
        )
    ).all()
    return {
        "regions": [{"id": rid, "name": name, "center": center} for rid, name, center in rows],
        "price_table": build_price_table(),
        "payments": PAYMENTS,
    }


class QuoteIn(BaseModel):
    region_ids: List[int] = Field(default_factory=list)


@router.post("/quote")
async def quote(payload: QuoteIn, request: Request, db: AsyncSession = Depends(get_db_session)):
    """Предварительная цена выбора — тем же ``quote_price``, что и при заказе."""
    await _current_client(request, db)
    targets = await client_orders.resolve_targets(db, payload.region_ids)
    return quote_price(len(targets))


# ----------------------------------------------------------------------
# Фото клиента (вне /static — отдача только владельцу карточки)
# ----------------------------------------------------------------------


def _client_photo_dir(client_id: int) -> Path:
    d = Path(__file__).resolve().parents[1] / "uploads" / "advertiser" / str(int(client_id))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _client_photo_paths(client_id: int) -> List[Path]:
    d = _client_photo_dir(client_id)
    return sorted(p for p in d.iterdir() if p.is_file() and p.suffix.lower() in _ALLOWED_IMG_EXT)


@router.get("/photos")
async def list_photos(request: Request, db: AsyncSession = Depends(get_db_session)):
    _user, client = await _current_client(request, db)
    return {
        "photos": [
            {"name": p.name, "size": p.stat().st_size} for p in _client_photo_paths(client.id)
        ]
    }


@router.post("/photos")
async def upload_photo(
    request: Request,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db_session),
):
    _user, client = await _current_client(request, db)
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in _ALLOWED_IMG_EXT:
        raise HTTPException(status_code=400, detail="Только JPG/PNG")
    data = await file.read()
    if len(data) > _MAX_IMG_BYTES:
        raise HTTPException(status_code=400, detail="Файл больше 12 МБ")
    if len(_client_photo_paths(client.id)) >= MAX_PHOTOS_PER_CLIENT:
        raise HTTPException(
            status_code=400, detail=f"Лимит {MAX_PHOTOS_PER_CLIENT} фото — удалите лишние"
        )
    name = f"{uuid.uuid4().hex}{suffix}"
    (_client_photo_dir(client.id) / name).write_bytes(data)
    return {"name": name, "size": len(data)}


@router.get("/photos/{name}")
async def get_photo(name: str, request: Request, db: AsyncSession = Depends(get_db_session)):
    _user, client = await _current_client(request, db)
    base = Path(str(name or "")).name  # отсечь path-traversal
    p = _client_photo_dir(client.id) / base
    if not p.is_file():
        raise HTTPException(status_code=404, detail="Фото не найдено")
    return FileResponse(p)


@router.delete("/photos/{name}")
async def delete_photo(name: str, request: Request, db: AsyncSession = Depends(get_db_session)):
    _user, client = await _current_client(request, db)
    base = Path(str(name or "")).name
    p = _client_photo_dir(client.id) / base
    if p.is_file():
        p.unlink()
    return {"success": True}


# ----------------------------------------------------------------------
# Заказы и посты
# ----------------------------------------------------------------------


class OrderIn(BaseModel):
    text: str = Field("", max_length=client_orders.TEXT_MAX)
    photos: List[str] = Field(default_factory=list)
    region_ids: List[int] = Field(default_factory=list)
    whole_network: bool = False
    publish_now: bool = False
    publish_at: Optional[str] = None  # "YYYY-MM-DDTHH:MM", МСК wall-clock


def _real_publisher_factory(db):
    async def factory(gid: int):
        from modules.publisher.vk_publisher_extended import VKPublisher

        return await VKPublisher.create_with_policy(db, target_group_id=gid)

    return factory


def _real_attachment_builder(client_id: int, user_token):
    """Заливка клиентских фото на стену группы (owner-specific, на каждую свою)."""

    def build(gid: int, image_names) -> List[str]:
        if not image_names or not user_token:
            return []
        wanted = {Path(n).name for n in image_names}
        paths = [p for p in _client_photo_paths(client_id) if p.name in wanted]
        if not paths:
            return []
        try:
            import vk_api

            from modules.publisher.vk_wall_photo_upload import upload_wall_images

            api = vk_api.VkApi(token=user_token).get_api()
            images = [p.read_bytes() for p in paths[:MAX_PHOTOS_PER_POST]]
            return upload_wall_images(api, images, group_id=gid)
        except Exception as e:  # noqa: BLE001 - пост уйдёт текстом
            logger.warning("client photo wall upload failed: %s", e)
            return []

    return build


def _parse_publish_at(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw).replace(tzinfo=None)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Некорректная дата: {raw}")


@router.post("/orders")
async def create_order(
    payload: OrderIn, request: Request, db: AsyncSession = Depends(get_db_session)
):
    """Создать заказ: районы галочками или «вся сеть», сразу или по расписанию.

    Не-trusted клиент получает ``pending`` (в VK не уходит ничего до одобрения
    владельцем) — UI честно показывает «после одобрения».
    """
    user, client = await _current_client(request, db)
    region_ids = payload.region_ids
    if payload.whole_network:
        region_ids = await client_orders.all_target_region_ids(db)

    from modules.vk_token_router import load_vk_routing

    user_token, _community_tokens = await load_vk_routing()

    try:
        result = await client_orders.submit_order(
            db,
            client=client,
            user_id=user.id,
            text=payload.text,
            image_paths=[Path(n).name for n in payload.photos][:MAX_PHOTOS_PER_POST],
            region_ids=region_ids,
            publish_at=_parse_publish_at(payload.publish_at),
            publish_now=payload.publish_now,
            publisher_factory=_real_publisher_factory(db),
            attachment_builder=_real_attachment_builder(client.id, user_token),
            msk_to_unix=_msk_to_unix,
        )
    except client_orders.OrderError as e:
        # Отказ формы — тоже событие для владельца: клиент ПЫТАЛСЯ заказать и
        # упёрся. Пишется не чаще раза в 10 минут на клиента (should-fix ревью:
        # зацикленный невалидный запрос не заливает ленту строкой на каждый 400).
        import asyncio

        from modules.ad_cabinet import owner_ping

        if await asyncio.to_thread(
            owner_ping.ping_dedup_pass, f"order_refused:{client.id}", ttl=600
        ):
            log_interaction(
                db,
                kind="cabinet_order_refused",
                client_id=client.id,
                summary=f"Заказ не прошёл валидацию: {e}",
                actor="client",
            )
            await db.commit()
        raise HTTPException(status_code=400, detail=str(e))

    log_interaction(
        db,
        kind="client_order",
        client_id=client.id,
        summary=f"Заказ из кабинета: {len(result['posts'])} районов, "
        f"{result['price_total']:.0f} ₽" + (" (на модерации)" if result["moderation"] else ""),
        actor="client",
    )
    await db.commit()

    if result["moderation"]:
        import asyncio

        await asyncio.to_thread(_notify_owner_pending, client, result)

    return {
        "order_ref": result["order_ref"],
        "price_total": result["price_total"],
        "quote": result["quote"],
        "moderation": result["moderation"],
        "posts": [p.to_dict() for p in result["posts"]],
    }


def _notify_owner_pending(client, result) -> None:
    """Telegram владельцу: новый заказ ждёт модерации (без дедупа — каждый
    заказ важен). Механика отправки — общая (``modules/ad_cabinet/owner_ping``)."""
    from modules.ad_cabinet.owner_ping import notify_owner

    notify_owner(
        f"🛎 Кабинет: клиент «{client.name or client.id}» создал заказ на "
        f"{len(result['posts'])} районов ({result['price_total']:.0f} ₽) — "
        "ждёт одобрения в /ad"
    )


@router.get("/posts")
async def my_posts(request: Request, db: AsyncSession = Depends(get_db_session)):
    """Мои посты: отложки (по заказам) + вышедшие публикации с метриками."""
    _user, client = await _current_client(request, db)
    scheduled = (
        (
            await db.execute(
                select(AdScheduledPost)
                .where(AdScheduledPost.client_id == client.id)
                .order_by(AdScheduledPost.id.desc())
                .limit(200)
            )
        )
        .scalars()
        .all()
    )
    publications = (
        (
            await db.execute(
                select(AdPublication)
                .where(AdPublication.client_id == client.id)
                .order_by(AdPublication.id.desc())
                .limit(200)
            )
        )
        .scalars()
        .all()
    )
    return {
        "scheduled": [r.to_dict() for r in scheduled],
        "publications": [r.to_dict() for r in publications],
    }


@router.post("/posts/{post_id}/cancel")
async def cancel_post(post_id: int, request: Request, db: AsyncSession = Depends(get_db_session)):
    """Отменить СВОЙ неопубликованный пост.

    ``pending``/``draft`` — просто отмена; ``scheduled`` — плюс снятие из
    VK-отложки (паттерн операторского cancel: VK-удаление не удалось → статус
    не меняем, клиент видит реальное состояние).
    """
    _user, client = await _current_client(request, db)
    row = await db.get(AdScheduledPost, post_id)
    if not row or row.client_id != client.id:
        raise HTTPException(status_code=404, detail="Пост не найден")
    if row.status in ("published", "cancelled", "rejected"):
        return row.to_dict()
    if row.status == "scheduled" and row.vk_postponed_post_id:
        from modules.publisher.vk_publisher_extended import VKPublisher

        publisher = await VKPublisher.create_with_policy(
            db, target_group_id=int(row.community_vk_id)
        )
        res = await publisher.delete_post(int(row.community_vk_id), int(row.vk_postponed_post_id))
        if not res.get("success"):
            return {**row.to_dict(), "cancel_error": res.get("error")}
    row.status = "cancelled"
    log_interaction(
        db,
        kind="cancelled",
        client_id=client.id,
        scheduled_post_id=row.id,
        summary="Клиент отменил пост из кабинета",
        actor="client",
    )
    await db.commit()
    await db.refresh(row)
    return row.to_dict()


# ----------------------------------------------------------------------
# Оплаты
# ----------------------------------------------------------------------


@router.get("/payments")
async def my_payments(request: Request, db: AsyncSession = Depends(get_db_session)):
    """Мои оплаты (awaiting/paid) + реквизиты для перевода (MVP: вручную)."""
    _user, client = await _current_client(request, db)
    rows = (
        (
            await db.execute(
                select(AdPayment)
                .where(AdPayment.client_id == client.id)
                .order_by(AdPayment.id.desc())
                .limit(200)
            )
        )
        .scalars()
        .all()
    )
    return {"payments": [r.to_dict() for r in rows], "requisites": PAYMENTS}


# ----------------------------------------------------------------------
# Чат с владельцем
# ----------------------------------------------------------------------


class ChatIn(BaseModel):
    body: str = Field(..., min_length=1, max_length=chat.BODY_MAX)


@router.get("/chat")
async def my_chat(
    request: Request,
    after_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db_session),
):
    _user, client = await _current_client(request, db)
    rows = await chat.fetch_thread(db, client.id, reader=chat.SENDER_CLIENT, after_id=after_id)
    await db.commit()  # отметки read_at
    return {"messages": [r.to_dict() for r in rows]}


@router.post("/chat")
async def send_chat(payload: ChatIn, request: Request, db: AsyncSession = Depends(get_db_session)):
    _user, client = await _current_client(request, db)
    try:
        row = await chat.post_message(db, client.id, chat.SENDER_CLIENT, payload.body)
    except chat.ChatError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await db.commit()
    await db.refresh(row)
    import asyncio

    from modules.ad_cabinet import owner_ping

    # Пинг о новом сообщении — не чаще раза в час на клиента: владелец узнаёт,
    # что клиент написал, не входя в /ad; переписка спамом в Telegram не льётся.
    await asyncio.to_thread(
        owner_ping.notify_owner,
        f"💬 Кабинет: сообщение от «{client.name or client.id}» — ответить в /ad → Кабинеты",
        dedup_key=f"chat:{client.id}",
    )
    return row.to_dict()
