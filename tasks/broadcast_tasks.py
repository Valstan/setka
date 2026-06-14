"""Celery-таски сетевой рассылки (директива brain 2026-06-14).

Диспетчер-публикатор раз в минуту + watchdog #018. Беат публикует wall.post
немедленно в назревшие кампании (НЕ в VK-отложку).
"""

from __future__ import annotations

import logging
from datetime import datetime

from tasks.celery_app import app
from utils.celery_asyncio import run_coro

logger = logging.getLogger(__name__)


@app.task(name="tasks.broadcast_tasks.dispatch_broadcasts")
def dispatch_broadcasts():
    """Тик диспетчера рассылки (каждую минуту): опубликовать назревшие кампании.

    No-op, если нет запланированных кампаний или BROADCAST_DISABLED. Публикация
    идемпотентна (per-(цель, прогон) защёлка), throttle ≥5с между постами.
    """
    try:
        from modules.broadcast.dispatcher import run_broadcast_dispatch

        result = run_coro(run_broadcast_dispatch())
        if result.get("dispatched"):
            logger.info("broadcast dispatch: %s", result)
        return {"success": True, "timestamp": datetime.now().isoformat(), **result}
    except Exception as e:
        logger.error(f"dispatch_broadcasts failed: {e}", exc_info=True)
        return {"success": False, "timestamp": datetime.now().isoformat(), "error": str(e)}


@app.task(name="tasks.broadcast_tasks.check_broadcast_heartbeat")
def check_broadcast_heartbeat():
    """Watchdog #018: алёрт в Telegram, если есть просроченные кампании рассылки."""
    try:
        from config.runtime import TELEGRAM_ALERT_CHAT_ID, TELEGRAM_TOKENS
        from modules.broadcast.dispatcher import maybe_alert_stale_broadcast

        token = TELEGRAM_TOKENS.get("VALSTANBOT") or TELEGRAM_TOKENS.get("ALERT")
        status = run_coro(
            maybe_alert_stale_broadcast(
                telegram_token=token,
                chat_id=TELEGRAM_ALERT_CHAT_ID,
            )
        )
        logger.info("broadcast watchdog: %s", status)
        return {"success": True, "status": status, "timestamp": datetime.now().isoformat()}
    except Exception as e:
        logger.error(f"check_broadcast_heartbeat failed: {e}", exc_info=True)
        return {"success": False, "timestamp": datetime.now().isoformat(), "error": str(e)}
