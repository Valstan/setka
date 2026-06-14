"""Подписка радара на источник — общий сервис для web-API и intake-бота.

Логика «resolved-мета → найти/создать/реактивировать RadarSource → найти/создать
RadarSubscription» жила в обработчике `web/api/radar.py:create_subscription`. Вынесена
сюда, чтобы её переиспускал и бот-приёмник каналов (`modules/radar/bot_intake.py`),
не дублируя upsert. Резолв источника (vk/rss/tg → {key,title,url}) остаётся за вызывающим
(у API — свой `_resolve_source_meta`; бот резолвит TG напрямую).
"""

from __future__ import annotations

from typing import Any, Dict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models_extended import RadarSource, RadarSubscription


async def upsert_subscription(
    session: AsyncSession,
    *,
    user_id: int,
    source_type: str,
    meta: Dict[str, Any],
) -> Dict[str, Any]:
    """Подписать юзера на источник (создаётся при первой подписке любым юзером).

    meta — результат resolve_source: {key, title, url}. Идемпотентно: повторная
    подписка не дублируется. Источник без активных подписок не удаляется, но
    реактивируется, когда на него снова подписались. Коммитит сам.

    Возвращает {created: bool, subscription_id: int, source: dict}.
    """
    source = (
        await session.execute(
            select(RadarSource).where(
                RadarSource.type == source_type, RadarSource.key == meta["key"]
            )
        )
    ).scalar_one_or_none()
    if source is None:
        source = RadarSource(
            type=source_type, key=meta["key"], title=meta.get("title"), url=meta.get("url")
        )
        session.add(source)
        await session.flush()
    elif not source.is_active:
        source.is_active = True  # реактивация: на источник снова подписались

    existing = (
        await session.execute(
            select(RadarSubscription).where(
                RadarSubscription.user_id == user_id,
                RadarSubscription.source_id == source.id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        await session.commit()
        return {"created": False, "subscription_id": existing.id, "source": source.to_dict()}

    sub = RadarSubscription(user_id=user_id, source_id=source.id)
    session.add(sub)
    await session.commit()
    await session.refresh(sub)
    return {"created": True, "subscription_id": sub.id, "source": source.to_dict()}
