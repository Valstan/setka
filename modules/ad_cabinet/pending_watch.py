"""Сторож pending-постов с прошедшей датой (аудит кабинета 2026-09-05).

Пост клиента ждёт одобрения владельца (``status='pending'``). Если владелец не
успел до назначенной даты, раньше никто об этом не узнавал: реконсилер берёт
только ``scheduled``, а позднее одобрение молча переносило выход «через три
минуты». Теперь: раз в час строки ``pending`` с ``publish_date`` старше часа
получают пометку в ``error_message``, владелец — пинг (дедуп на строку,
сутки), клиент — одно ВК-уведомление «дата прошла, владелец назначит новую».
Статус не меняется: слот дня и пакет остаются за постом до решения владельца.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Optional

from sqlalchemy import select

from database.models import AdScheduledPost

logger = logging.getLogger(__name__)

MSK = timezone(timedelta(hours=3))
GRACE = timedelta(hours=1)
OWNER_PING_TTL = 24 * 3600
MARK = "дата прошла — ждёт новой даты при одобрении"


def _default_owner_ping(text: str, key: str) -> None:  # pragma: no cover - сеть
    from modules.ad_cabinet import owner_ping

    try:
        owner_ping.notify_owner(text, dedup_key=key, dedup_ttl=OWNER_PING_TTL)
    except Exception:
        logger.warning("pending watch ping failed", exc_info=True)


async def run_pending_watch(
    *,
    session_factory: Optional[Callable] = None,
    now: Optional[datetime] = None,
    owner_ping: Optional[Callable[[str, str], Any]] = None,
    client_notify: Optional[Callable[[Any, int, str], Any]] = None,
) -> Dict[str, Any]:
    """Пометить просроченные pending и уведомить обе стороны. Возвращает счётчики."""
    if session_factory is None:
        from database.connection import AsyncSessionLocal

        session_factory = AsyncSessionLocal
    now = now or datetime.now(MSK).replace(tzinfo=None)
    if owner_ping is None:

        def owner_ping(text: str, key: str):  # type: ignore[no-redef]
            return asyncio.get_running_loop().run_in_executor(None, _default_owner_ping, text, key)

    if client_notify is None:
        from modules.ad_cabinet.vk_bot import notify as vk_notify

        client_notify = vk_notify.notify_client

    marked = 0
    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(AdScheduledPost).where(
                        AdScheduledPost.status == "pending",
                        AdScheduledPost.publish_date <= now - GRACE,
                    )
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            first_time = row.error_message != MARK
            row.error_message = MARK
            res = owner_ping(
                f"⌛ Пост №{row.id} клиента {row.client_id} ждал одобрения, а дата выхода "
                f"{row.publish_date:%d.%m %H:%M} уже прошла — одобри с новой датой или отклони.",
                f"pending_overdue:{row.id}",
            )
            if hasattr(res, "__await__"):
                await res
            if first_time and row.client_id:
                try:
                    when = f"{row.publish_date:%d.%m %H:%M}"
                    res2 = client_notify(
                        session,
                        row.client_id,
                        f"⌛ Ваш пост на {when} ещё не одобрен, дата прошла — "
                        "владелец назначит новую или свяжется с вами.",
                    )
                    if hasattr(res2, "__await__"):
                        await res2
                except Exception:  # noqa: BLE001 — уведомление не важнее пометки
                    logger.warning("pending watch client notify failed", exc_info=True)
            marked += 1
        await session.commit()
    return {"checked": len(rows), "marked": marked}
