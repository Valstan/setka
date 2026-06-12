"""Web-push радара (Ф0.5): VAPID-ключи + рассылка по новым элементам.

VAPID: приватный ключ — env ``RADAR_VAPID_PRIVATE_KEY`` (base64url raw EC
P-256, #008), subject — ``RADAR_VAPID_SUBJECT`` (mailto:). Публичный ключ
выводится из приватного на лету (отдаётся фронту для pushManager.subscribe).

Рассылка дёргается поллером после коммита: новые элементы → fan-out по
подпискам юзеров этих источников, один push на юзера за прогон. pywebpush
синхронный (requests) — каждый вызов уводим в thread. 404/410 от
push-сервиса = подписка умерла → удаляем строку. Никогда не raises —
проблемы пуша не должны ломать поллинг.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from datetime import datetime
from typing import Dict, Optional

from sqlalchemy import select

logger = logging.getLogger(__name__)

PUSH_TTL_SECONDS = 3600  # уведомление о новых постах протухает за час


def _vapid():
    from py_vapid import Vapid

    private = os.getenv("RADAR_VAPID_PRIVATE_KEY")
    if not private:
        return None
    try:
        return Vapid.from_string(private)
    except Exception as e:  # noqa: BLE001 - битый ключ = push выключен
        logger.warning("radar push: invalid RADAR_VAPID_PRIVATE_KEY: %s", e)
        return None


def vapid_public_key() -> Optional[str]:
    """Публичный VAPID-ключ (base64url uncompressed point) для фронта."""
    vapid = _vapid()
    if vapid is None:
        return None
    from cryptography.hazmat.primitives import serialization

    raw = vapid.public_key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def push_configured() -> bool:
    return bool(os.getenv("RADAR_VAPID_PRIVATE_KEY"))


def _send_webpush_sync(subscription, payload: str) -> Optional[int]:
    """Один push; возвращает HTTP-статус ошибки (404/410/...) либо None при успехе."""
    from pywebpush import WebPushException, webpush

    try:
        webpush(
            subscription_info={
                "endpoint": subscription.endpoint,
                "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth},
            },
            data=payload,
            vapid_private_key=os.getenv("RADAR_VAPID_PRIVATE_KEY"),
            vapid_claims={"sub": os.getenv("RADAR_VAPID_SUBJECT", "mailto:admin@example.com")},
            ttl=PUSH_TTL_SECONDS,
        )
        return None
    except WebPushException as e:
        status = getattr(getattr(e, "response", None), "status_code", None)
        logger.info("radar push failed (%s): %s", status, e)
        return status or -1


async def notify_new_items(new_by_source: Dict[int, int]) -> dict:
    """Push «N новых» юзерам, подписанным на источники с новыми элементами.

    ``new_by_source`` — {source_id: new_count} из прогона поллера.
    Возвращает сводку {users, sent, dropped}; никогда не raises.
    """
    summary = {"users": 0, "sent": 0, "dropped": 0}
    source_ids = [sid for sid, n in (new_by_source or {}).items() if n > 0]
    if not source_ids or not push_configured():
        return summary

    try:
        from database import models  # noqa: F401 - конфигурация мапперов (PR #189)
        from database.connection import AsyncSessionLocal
        from database.models_extended import RadarPushSubscription, RadarSubscription

        async with AsyncSessionLocal() as session:
            rows = (
                await session.execute(
                    select(RadarSubscription.user_id, RadarSubscription.source_id).where(
                        RadarSubscription.source_id.in_(source_ids)
                    )
                )
            ).all()
            new_by_user: Dict[int, int] = {}
            for user_id, source_id in rows:
                new_by_user[user_id] = new_by_user.get(user_id, 0) + new_by_source[source_id]
            if not new_by_user:
                return summary

            subs = (
                (
                    await session.execute(
                        select(RadarPushSubscription).where(
                            RadarPushSubscription.user_id.in_(list(new_by_user))
                        )
                    )
                )
                .scalars()
                .all()
            )
            summary["users"] = len({s.user_id for s in subs})

            for sub in subs:
                count = new_by_user.get(sub.user_id, 0)
                payload = json.dumps(
                    {
                        "title": "Радар",
                        "body": f"Новых элементов в ленте: {count}",
                        "url": "/radar",
                    },
                    ensure_ascii=False,
                )
                status = await asyncio.to_thread(_send_webpush_sync, sub, payload)
                if status is None:
                    sub.last_success_at = datetime.utcnow()
                    summary["sent"] += 1
                elif status in (404, 410):  # подписка умерла — чистим
                    await session.delete(sub)
                    summary["dropped"] += 1
            await session.commit()
    except Exception as e:  # noqa: BLE001 - push best-effort
        logger.warning("radar push: notify_new_items failed: %s", e)
    return summary
