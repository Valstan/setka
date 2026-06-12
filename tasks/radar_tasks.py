"""Celery-таски контент-радара (Ф0.2): fan-out поллер + watchdog #018."""

from __future__ import annotations

import logging
from datetime import datetime

from tasks.celery_app import app
from utils.celery_asyncio import run_coro

logger = logging.getLogger(__name__)


@app.task(name="tasks.radar_tasks.poll_radar_sources")
def poll_radar_sources():
    """Поллинг всех активных подписанных источников радара (каждые 10 мин).

    Источник поллится один раз на всех подписчиков (fan-out); дедуп — на
    уровне БД (uniq source_id+external_id). Успешный прогон пишет heartbeat
    ``setka:radar_last_polled``.
    """
    try:
        from modules.radar.poller import poll_all_sources

        result = run_coro(poll_all_sources())
        return {"success": True, "timestamp": datetime.now().isoformat(), **result}
    except Exception as e:
        logger.error(f"poll_radar_sources failed: {e}", exc_info=True)
        return {"success": False, "timestamp": datetime.now().isoformat(), "error": str(e)}


@app.task(name="tasks.radar_tasks.cleanup_old_radar_items")
def cleanup_old_radar_items():
    """Ретенция ленты радара: элементы старше N дней удаляются (03:20).

    Сохранёнки не страдают: radar_saved — снимок контента, его item_id
    гаснет в NULL (FK ON DELETE SET NULL). Порог — env
    RADAR_ITEMS_RETENTION_DAYS (дефолт 30, план Ф0).
    """
    try:
        from modules.radar.poller import cleanup_old_items

        result = run_coro(cleanup_old_items())
        logger.info("radar items cleanup: %s", result)
        return {"success": True, "timestamp": datetime.now().isoformat(), **result}
    except Exception as e:
        logger.error(f"cleanup_old_radar_items failed: {e}", exc_info=True)
        return {"success": False, "timestamp": datetime.now().isoformat(), "error": str(e)}


@app.task(name="tasks.radar_tasks.check_radar_poll_heartbeat")
def check_radar_poll_heartbeat():
    """Watchdog: алёрт в Telegram, если поллер радара молчит при живых подписках."""
    try:
        from config.runtime import TELEGRAM_ALERT_CHAT_ID, TELEGRAM_TOKENS
        from modules.radar.poller import maybe_alert_stale_radar_poll

        token = TELEGRAM_TOKENS.get("VALSTANBOT") or TELEGRAM_TOKENS.get("ALERT")
        status = run_coro(
            maybe_alert_stale_radar_poll(
                telegram_token=token,
                chat_id=TELEGRAM_ALERT_CHAT_ID,
            )
        )
        logger.info("radar poll watchdog: %s", status)
        return {"success": True, "status": status, "timestamp": datetime.now().isoformat()}
    except Exception as e:
        logger.error(f"check_radar_poll_heartbeat failed: {e}", exc_info=True)
        return {"success": False, "timestamp": datetime.now().isoformat(), "error": str(e)}
