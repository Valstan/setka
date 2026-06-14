"""API контент-радара (Ф0.2): подписки на источники + лента.

Доступ: и radar-юзер, и оператор (префикс ``/api/radar/`` входит в
RADAR_PREFIXES auth-гейта; оператора гейт пускает всюду). Текущий юзер —
``request.state.user``, его кладёт AuthGateMiddleware; все данные строго
свои (фильтр по user_id), чужие подписки недостижимы.

Fan-out: источник в ``radar_sources`` один на всех, подписка лишь связывает
юзера с ним. Удаление последней подписки источник не удаляет — он перестаёт
поллиться сам (поллер берёт только источники с ≥1 подпиской).
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from database import models  # noqa: F401 - конфигурация мапперов (PR #189)
from database.connection import AsyncSessionLocal
from database.models_extended import (
    RadarItem,
    RadarOutput,
    RadarPushSubscription,
    RadarSaved,
    RadarSource,
    RadarSubscription,
    RadarUser,
)

logger = logging.getLogger(__name__)
router = APIRouter()

FEED_MAX_LIMIT = 100


def _current_user(request: Request):
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


class SubscriptionCreateIn(BaseModel):
    type: str = Field(..., pattern=r"^(vk|rss|tg)$")  # tg — через egress-relay (Ф0.3)
    value: str = Field(..., min_length=1, max_length=1024)


async def _resolve_source_meta(source_type: str, value: str) -> dict:
    """Сырой ввод юзера → {key, title, url}; ValueError при невалидном источнике."""
    if source_type == "vk":
        from modules.radar.sources.vk import resolve_source

        return await resolve_source(value)

    if source_type == "tg":
        from modules.radar.sources.tg import resolve_source as resolve_tg

        return await resolve_tg(value)

    # rss: лёгкая валидация формы + проба фида (и заодно title для UI).
    url = value.strip()
    if not url.startswith(("http://", "https://")):
        raise ValueError("RSS-источник должен быть http(s)-URL")
    try:
        import feedparser
        import httpx

        from modules.radar.sources.rss import FETCH_TIMEOUT_SECONDS, USER_AGENT

        async with httpx.AsyncClient(
            timeout=FETCH_TIMEOUT_SECONDS,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
        parsed = feedparser.parse(response.content)
        if not parsed.entries and parsed.get("bozo"):
            raise ValueError("По этому URL не нашёлся валидный RSS/Atom-фид")
        title = (parsed.feed.get("title") or "").strip() or None
    except ValueError:
        raise
    except Exception as e:  # noqa: BLE001 - сеть/HTTP → человекочитаемая 400
        raise ValueError(f"Фид недоступен: {e}") from e
    return {"key": url, "title": title, "url": url}


@router.get("/subscriptions")
async def list_subscriptions(request: Request):
    """Подписки текущего юзера с метаданными источников."""
    user = _current_user(request)
    async with AsyncSessionLocal() as session:
        subs = (
            (
                await session.execute(
                    select(RadarSubscription)
                    .where(RadarSubscription.user_id == user.id)
                    .order_by(RadarSubscription.id)
                )
            )
            .scalars()
            .all()
        )
        return {
            "subscriptions": [
                {
                    "id": s.id,
                    "is_active": s.is_active,
                    "created_at": s.created_at.isoformat() if s.created_at else None,
                    "source": s.source.to_dict() if s.source else None,
                }
                for s in subs
            ]
        }


class SubscriptionPatchIn(BaseModel):
    is_active: bool


@router.patch("/subscriptions/{subscription_id}")
async def patch_subscription(subscription_id: int, body: SubscriptionPatchIn, request: Request):
    """Пауза/возобновление источника без удаления (fan-out сохраняется)."""
    user = _current_user(request)
    async with AsyncSessionLocal() as session:
        sub = (
            await session.execute(
                select(RadarSubscription).where(
                    RadarSubscription.id == subscription_id,
                    RadarSubscription.user_id == user.id,
                )
            )
        ).scalar_one_or_none()
        if sub is None:
            raise HTTPException(status_code=404, detail="Подписка не найдена")
        sub.is_active = body.is_active
        await session.commit()
        return {"id": sub.id, "is_active": sub.is_active}


@router.post("/subscriptions", status_code=201)
async def create_subscription(body: SubscriptionCreateIn, request: Request):
    """Подписаться на источник (создаётся при первой подписке любым юзером)."""
    user = _current_user(request)
    try:
        meta = await _resolve_source_meta(body.type, body.value)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:  # токен не сконфигурирован и т.п.
        logger.error("radar source resolve failed: %s", e)
        raise HTTPException(status_code=503, detail="Источник временно нельзя проверить")

    from modules.radar.subscriptions import upsert_subscription

    async with AsyncSessionLocal() as session:
        res = await upsert_subscription(session, user_id=user.id, source_type=body.type, meta=meta)
        return {
            "subscription_id": res["subscription_id"],
            "source": res["source"],
            "created": res["created"],
        }


@router.delete("/subscriptions/{subscription_id}")
async def delete_subscription(subscription_id: int, request: Request):
    """Отписаться. Источник остаётся (fan-out), но без подписок не поллится."""
    user = _current_user(request)
    async with AsyncSessionLocal() as session:
        sub = (
            await session.execute(
                select(RadarSubscription).where(
                    RadarSubscription.id == subscription_id,
                    RadarSubscription.user_id == user.id,
                )
            )
        ).scalar_one_or_none()
        if sub is None:
            raise HTTPException(status_code=404, detail="Подписка не найдена")
        await session.delete(sub)
        await session.commit()
    return {"deleted": True}


@router.get("/feed")
async def get_feed(
    request: Request,
    before_id: Optional[int] = Query(None, description="курсор: элементы старше этого id"),
    limit: int = Query(30, ge=1, le=FEED_MAX_LIMIT),
):
    """Лента текущего юзера: элементы его источников, свежее сверху.

    Курсор — по ``radar_items.id`` (монотонный BIGSERIAL): стабильнее
    published_at (фиды любят его менять) и индекс уже есть (PK).
    """
    user = _current_user(request)
    async with AsyncSessionLocal() as session:
        # Только активные подписки: поставленный на паузу источник уходит из ленты
        # (но продолжает поллиться для других — fan-out).
        source_ids = select(RadarSubscription.source_id).where(
            RadarSubscription.user_id == user.id,
            RadarSubscription.is_active.is_(True),
        )
        stmt = (
            select(RadarItem, RadarSource)
            .join(RadarSource, RadarSource.id == RadarItem.source_id)
            .where(RadarItem.source_id.in_(source_ids))
            .order_by(RadarItem.id.desc())
            .limit(limit)
        )
        if before_id is not None:
            stmt = stmt.where(RadarItem.id < before_id)
        rows = (await session.execute(stmt)).all()

    items = []
    for item, source in rows:
        payload = item.to_dict()
        payload["source"] = {
            "id": source.id,
            "type": source.type,
            "title": source.title,
            "url": source.url,
        }
        items.append(payload)
    next_cursor = items[-1]["id"] if len(items) == limit else None
    return {
        "items": items,
        "next_before_id": next_cursor,
        "last_seen_item_id": getattr(user, "last_seen_item_id", None),
    }


class SeenIn(BaseModel):
    item_id: int = Field(..., ge=1)


@router.post("/feed/seen")
async def mark_seen(body: SeenIn, request: Request):
    """Сдвинуть курсор новизны вперёд (назад не двигается)."""
    user = _current_user(request)
    async with AsyncSessionLocal() as session:
        db_user = await session.get(RadarUser, user.id)
        if db_user is None:
            raise HTTPException(status_code=404, detail="Пользователь не найден")
        if (db_user.last_seen_item_id or 0) < body.item_id:
            db_user.last_seen_item_id = body.item_id
            await session.commit()
        return {"last_seen_item_id": db_user.last_seen_item_id}


# ───────────────────────── Save-архив (Ф0.4) ─────────────────────────


class SaveIn(BaseModel):
    item_id: int = Field(..., ge=1)


@router.get("/saved")
async def list_saved(
    request: Request,
    before_id: Optional[int] = Query(None),
    limit: int = Query(30, ge=1, le=FEED_MAX_LIMIT),
):
    """Архив текущего юзера, свежее сверху, курсор по id."""
    user = _current_user(request)
    async with AsyncSessionLocal() as session:
        stmt = (
            select(RadarSaved)
            .where(RadarSaved.user_id == user.id)
            .order_by(RadarSaved.id.desc())
            .limit(limit)
        )
        if before_id is not None:
            stmt = stmt.where(RadarSaved.id < before_id)
        saved = (await session.execute(stmt)).scalars().all()
        used = (
            await session.execute(
                select(func.coalesce(func.sum(RadarSaved.archived_bytes), 0)).where(
                    RadarSaved.user_id == user.id
                )
            )
        ).scalar()
        global_used = (
            await session.execute(select(func.coalesce(func.sum(RadarUser.used_bytes), 0)))
        ).scalar()
    items = [s.to_dict() for s in saved]
    # Box-level статус архива (Ф1) — чтобы UI показал «архив заполнен», а не молча
    # ронял новые фото в ссылки.
    from modules.radar.archive import disk_free_bytes, max_archive_bytes, min_free_bytes

    free = disk_free_bytes()
    min_free = min_free_bytes()
    max_arch = max_archive_bytes()
    return {
        "items": items,
        "next_before_id": items[-1]["id"] if len(items) == limit else None,
        "used_bytes": int(used or 0),
        "quota_bytes": getattr(user, "quota_bytes", None),
        "archive": {
            "global_used_bytes": int(global_used or 0),
            "max_bytes": max_arch,
            "disk_free_bytes": free,
            "min_free_bytes": min_free,
            "writable": free - min_free > 0 and int(global_used or 0) < max_arch,
        },
    }


@router.post("/saved", status_code=201)
async def save_item(body: SaveIn, request: Request):
    """Сохранить элемент СВОЕЙ ленты в архив (снимок + скачивание фото).

    Квота предупредительная (Ф0): текст сохраняется всегда, фото качаются,
    пока юзер помещается в quota_bytes, дальше остаются ссылками.
    """
    user = _current_user(request)
    from modules.radar.archive import download_media, max_archive_bytes

    async with AsyncSessionLocal() as session:
        # Элемент должен принадлежать источнику, на который юзер подписан.
        item = (
            await session.execute(
                select(RadarItem)
                .join(
                    RadarSubscription,
                    RadarSubscription.source_id == RadarItem.source_id,
                )
                .where(RadarItem.id == body.item_id, RadarSubscription.user_id == user.id)
            )
        ).scalar_one_or_none()
        if item is None:
            raise HTTPException(status_code=404, detail="Элемент не найден в вашей ленте")

        existing = (
            await session.execute(
                select(RadarSaved).where(
                    RadarSaved.user_id == user.id, RadarSaved.item_id == item.id
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return {"saved": existing.to_dict(), "created": False}

        source = await session.get(RadarSource, item.source_id)
        saved = RadarSaved(
            user_id=user.id,
            item_id=item.id,
            source_title=source.title if source else None,
            url=item.url,
            title=item.title,
            text=item.text,
            media=item.media or [],
            published_at=item.published_at,
        )
        session.add(saved)
        await session.flush()  # нужен saved.id для каталога на диске

        db_user = await session.get(RadarUser, user.id)
        # quota_left = min(остаток per-user квоты, остаток глобального потолка
        # архива всех юзеров). Box-level enforcement (Ф1): защищаем 10-ГБ бокс от
        # переполнения суммой архивов; диск-floor дополнительно держит archive.py.
        per_user_left = max(0, (db_user.quota_bytes or 0) - (db_user.used_bytes or 0))
        global_used = (
            await session.execute(select(func.coalesce(func.sum(RadarUser.used_bytes), 0)))
        ).scalar() or 0
        global_left = max(0, max_archive_bytes() - int(global_used))
        quota_left = max(0, min(per_user_left, global_left))
        try:
            media, downloaded = await download_media(
                item.media or [], user.id, saved.id, quota_left=quota_left
            )
        except Exception as e:  # noqa: BLE001 - архив без медиа лучше, чем 500
            logger.warning("radar save: media archive failed: %s", e)
            media, downloaded = item.media or [], 0
        saved.media = media
        saved.archived_bytes = downloaded
        db_user.used_bytes = (db_user.used_bytes or 0) + downloaded

        await session.commit()
        await session.refresh(saved)
        return {"saved": saved.to_dict(), "created": True}


@router.delete("/saved/{saved_id}")
async def delete_saved(saved_id: int, request: Request):
    """Удалить сохранёнку: запись + файлы с диска, вернуть байты в квоту."""
    user = _current_user(request)
    from modules.radar.archive import remove_saved_dir

    async with AsyncSessionLocal() as session:
        saved = (
            await session.execute(
                select(RadarSaved).where(RadarSaved.id == saved_id, RadarSaved.user_id == user.id)
            )
        ).scalar_one_or_none()
        if saved is None:
            raise HTTPException(status_code=404, detail="Сохранёнка не найдена")
        db_user = await session.get(RadarUser, user.id)
        if db_user is not None:
            db_user.used_bytes = max(0, (db_user.used_bytes or 0) - (saved.archived_bytes or 0))
        await session.delete(saved)
        await session.commit()
    remove_saved_dir(user.id, saved_id)
    return {"deleted": True}


@router.get("/saved/{saved_id}/media/{filename}")
async def get_saved_media(saved_id: int, filename: str, request: Request):
    """Отдать заархивированный файл сохранёнки (только владельцу)."""
    user = _current_user(request)
    from modules.radar.archive import media_file_path

    async with AsyncSessionLocal() as session:
        saved = (
            await session.execute(
                select(RadarSaved.id).where(
                    RadarSaved.id == saved_id, RadarSaved.user_id == user.id
                )
            )
        ).scalar_one_or_none()
    if saved is None:
        raise HTTPException(status_code=404, detail="Сохранёнка не найдена")
    path = media_file_path(user.id, saved_id, filename)
    if path is None:
        raise HTTPException(status_code=404, detail="Файл не найден")
    return FileResponse(path)


# ───────────────────────── Web-push (Ф0.5) ─────────────────────────


class PushKeysIn(BaseModel):
    p256dh: str = Field(..., min_length=1, max_length=256)
    auth: str = Field(..., min_length=1, max_length=128)


class PushSubscriptionIn(BaseModel):
    endpoint: str = Field(..., min_length=10, max_length=1024)
    keys: PushKeysIn


@router.get("/push/vapid-public-key")
async def get_vapid_public_key(request: Request):
    """Публичный VAPID-ключ для pushManager.subscribe; 404 = push не настроен."""
    _current_user(request)
    from modules.radar.push import vapid_public_key

    key = vapid_public_key()
    if not key:
        raise HTTPException(status_code=404, detail="Push не настроен на сервере")
    return {"key": key}


@router.post("/push/subscriptions", status_code=201)
async def create_push_subscription(body: PushSubscriptionIn, request: Request):
    """Зарегистрировать браузерную push-подписку (идемпотентно по endpoint)."""
    user = _current_user(request)
    async with AsyncSessionLocal() as session:
        existing = (
            await session.execute(
                select(RadarPushSubscription).where(RadarPushSubscription.endpoint == body.endpoint)
            )
        ).scalar_one_or_none()
        if existing is not None:
            # Браузер мог пересоздать ключи / юзер перелогинился — обновляем.
            existing.user_id = user.id
            existing.p256dh = body.keys.p256dh
            existing.auth = body.keys.auth
            await session.commit()
            return {"subscription_id": existing.id, "created": False}
        sub = RadarPushSubscription(
            user_id=user.id,
            endpoint=body.endpoint,
            p256dh=body.keys.p256dh,
            auth=body.keys.auth,
        )
        session.add(sub)
        await session.commit()
        await session.refresh(sub)
        return {"subscription_id": sub.id, "created": True}


class PushUnsubscribeIn(BaseModel):
    endpoint: str = Field(..., min_length=10, max_length=1024)


@router.post("/push/unsubscribe")
async def delete_push_subscription(body: PushUnsubscribeIn, request: Request):
    """Снять push-подписку по endpoint (только свою)."""
    user = _current_user(request)
    async with AsyncSessionLocal() as session:
        sub = (
            await session.execute(
                select(RadarPushSubscription).where(
                    RadarPushSubscription.endpoint == body.endpoint,
                    RadarPushSubscription.user_id == user.id,
                )
            )
        ).scalar_one_or_none()
        if sub is not None:
            await session.delete(sub)
            await session.commit()
    return {"deleted": sub is not None}


# ───────────────────── Целевые каналы вывода (кабинет, 045) ─────────────────────


_OUTPUT_TYPES = ("feed", "telegram", "vk")
_OUTPUT_MODES = ("excerpt_link", "full")


class OutputCreateIn(BaseModel):
    type: str = Field(..., pattern=r"^(feed|telegram|vk)$")
    title: Optional[str] = Field(None, max_length=200)
    target: Optional[str] = Field(None, max_length=512)
    mode: str = Field("excerpt_link", pattern=r"^(excerpt_link|full)$")
    bot_name: Optional[str] = Field(None, max_length=64)  # для telegram; пусто = радар-бот


class OutputPatchIn(BaseModel):
    title: Optional[str] = Field(None, max_length=200)
    target: Optional[str] = Field(None, max_length=512)
    mode: Optional[str] = Field(None, pattern=r"^(excerpt_link|full)$")
    bot_name: Optional[str] = Field(None, max_length=64)
    is_active: Optional[bool] = None


@router.get("/outputs")
async def list_outputs(request: Request):
    """Целевые каналы вывода текущего юзера (куда радар шлёт найденное)."""
    user = _current_user(request)
    async with AsyncSessionLocal() as session:
        outputs = (
            (
                await session.execute(
                    select(RadarOutput)
                    .where(RadarOutput.user_id == user.id)
                    .order_by(RadarOutput.id)
                )
            )
            .scalars()
            .all()
        )
        return {"outputs": [o.to_dict() for o in outputs]}


@router.post("/outputs", status_code=201)
async def create_output(body: OutputCreateIn, request: Request):
    """Добавить целевой канал вывода. Внешние (tg/vk) требуют target.

    Курсор доставки стартует с текущего MAX(item.id) — новый вывод шлёт только
    то, что пришло ПОСЛЕ подключения, не выстреливает накопленным бэклогом.
    """
    user = _current_user(request)
    if body.type in ("telegram", "vk") and not (body.target or "").strip():
        raise HTTPException(
            status_code=400, detail="Для этого типа нужен адрес назначения (target)"
        )
    config = {"bot_name": body.bot_name.strip().upper()} if (body.bot_name or "").strip() else None

    from modules.radar.delivery import max_item_id

    async with AsyncSessionLocal() as session:
        cursor = await max_item_id(session)
        output = RadarOutput(
            user_id=user.id,
            type=body.type,
            title=(body.title or "").strip() or None,
            target=(body.target or "").strip() or None,
            mode=body.mode,
            config=config,
            last_item_id=cursor,
        )
        session.add(output)
        await session.commit()
        await session.refresh(output)
        return output.to_dict()


@router.patch("/outputs/{output_id}")
async def patch_output(output_id: int, body: OutputPatchIn, request: Request):
    """Редактировать вывод: метка / адрес / режим / бот / вкл-выкл."""
    user = _current_user(request)
    async with AsyncSessionLocal() as session:
        output = (
            await session.execute(
                select(RadarOutput).where(
                    RadarOutput.id == output_id, RadarOutput.user_id == user.id
                )
            )
        ).scalar_one_or_none()
        if output is None:
            raise HTTPException(status_code=404, detail="Вывод не найден")
        if body.title is not None:
            output.title = body.title.strip() or None
        if body.target is not None:
            output.target = body.target.strip() or None
        if body.mode is not None:
            output.mode = body.mode
        if body.bot_name is not None:
            cfg = dict(output.config or {})
            name = body.bot_name.strip().upper()
            if name:
                cfg["bot_name"] = name
            else:
                cfg.pop("bot_name", None)
            output.config = cfg or None
        if body.is_active is not None:
            output.is_active = body.is_active
        await session.commit()
        await session.refresh(output)
        return output.to_dict()


@router.delete("/outputs/{output_id}")
async def delete_output(output_id: int, request: Request):
    """Удалить целевой канал вывода (только свой)."""
    user = _current_user(request)
    async with AsyncSessionLocal() as session:
        output = (
            await session.execute(
                select(RadarOutput).where(
                    RadarOutput.id == output_id, RadarOutput.user_id == user.id
                )
            )
        ).scalar_one_or_none()
        if output is None:
            raise HTTPException(status_code=404, detail="Вывод не найден")
        await session.delete(output)
        await session.commit()
    return {"deleted": True}


@router.post("/outputs/{output_id}/test")
async def test_output(output_id: int, request: Request):
    """Отправить тестовый элемент в канал — проверить доставку до того, как
    положиться на вывод (probe на уровне пользователя)."""
    user = _current_user(request)
    from modules.radar.delivery import send_test_output

    result = await send_test_output(output_id=output_id, user_id=user.id)
    if not result.get("ok") and result.get("detail") == "Вывод не найден":
        raise HTTPException(status_code=404, detail="Вывод не найден")
    return result
