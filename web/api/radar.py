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
from pydantic import BaseModel, Field
from sqlalchemy import select

from database import models  # noqa: F401 - конфигурация мапперов (PR #189)
from database.connection import AsyncSessionLocal
from database.models_extended import RadarItem, RadarSource, RadarSubscription

logger = logging.getLogger(__name__)
router = APIRouter()

FEED_MAX_LIMIT = 100


def _current_user(request: Request):
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


class SubscriptionCreateIn(BaseModel):
    type: str = Field(..., pattern=r"^(vk|rss)$")  # tg — Ф0.3 (egress-relay)
    value: str = Field(..., min_length=1, max_length=1024)


async def _resolve_source_meta(source_type: str, value: str) -> dict:
    """Сырой ввод юзера → {key, title, url}; ValueError при невалидном источнике."""
    if source_type == "vk":
        from modules.radar.sources.vk import resolve_source

        return await resolve_source(value)

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
                    "created_at": s.created_at.isoformat() if s.created_at else None,
                    "source": s.source.to_dict() if s.source else None,
                }
                for s in subs
            ]
        }


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

    async with AsyncSessionLocal() as session:
        source = (
            await session.execute(
                select(RadarSource).where(
                    RadarSource.type == body.type, RadarSource.key == meta["key"]
                )
            )
        ).scalar_one_or_none()
        if source is None:
            source = RadarSource(
                type=body.type, key=meta["key"], title=meta["title"], url=meta["url"]
            )
            session.add(source)
            await session.flush()
        elif not source.is_active:
            source.is_active = True  # реактивация: на источник снова подписались

        existing = (
            await session.execute(
                select(RadarSubscription).where(
                    RadarSubscription.user_id == user.id,
                    RadarSubscription.source_id == source.id,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return {"subscription_id": existing.id, "source": source.to_dict(), "created": False}

        sub = RadarSubscription(user_id=user.id, source_id=source.id)
        session.add(sub)
        await session.commit()
        await session.refresh(sub)
        return {"subscription_id": sub.id, "source": source.to_dict(), "created": True}


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
        source_ids = select(RadarSubscription.source_id).where(RadarSubscription.user_id == user.id)
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
    return {"items": items, "next_before_id": next_cursor}
