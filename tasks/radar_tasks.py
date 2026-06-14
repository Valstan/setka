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


async def _run_radar_bot():
    """Один тик intake-бота «Карман»: getUpdates → добавить каналы из форвардов."""
    from config.runtime import (
        TELEGRAM_TOKENS,
        get_radar_bot_allowed_users,
        get_radar_bot_name,
        get_radar_bot_radar_user_id,
    )

    name = get_radar_bot_name()
    if not name:
        return {"skipped": "RADAR_BOT_NAME not set"}
    token = TELEGRAM_TOKENS.get(name)
    if not token:
        return {"skipped": f"no TELEGRAM_TOKEN for {name}"}

    allowed = get_radar_bot_allowed_users()
    if not allowed:
        # Без allowlist приёмник никому не отвечает (молчит чужим) — не поллим
        # вовсе, чтобы зря не тревожить общий бот. Владелец задаёт свой tg-id.
        return {"skipped": "RADAR_BOT_ALLOWED_USERS empty"}

    from modules.digest_heartbeat import _get_redis

    r = _get_redis()
    if r is None:
        return {"skipped": "no redis (offset persistence required)"}
    offset_key = "setka:radar_bot_offset"

    def offset_get():
        v = r.get(offset_key)
        try:
            return int(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    def offset_set(v):
        r.set(offset_key, int(v))

    radar_user_id = get_radar_bot_radar_user_id()

    async def add_channel(username):
        from database.connection import AsyncSessionLocal
        from modules.radar.sources.tg import resolve_source
        from modules.radar.subscriptions import upsert_subscription

        try:
            meta = await resolve_source(username)
        except Exception as e:  # noqa: BLE001 - канал недоступен/приватный
            return {"status": "error", "error": str(e)[:160]}
        async with AsyncSessionLocal() as session:
            res = await upsert_subscription(
                session, user_id=radar_user_id, source_type="tg", meta=meta
            )
        return {
            "status": "added" if res["created"] else "exists",
            "title": meta.get("title"),
        }

    from modules.radar.bot_intake import poll_radar_bot_once

    return await poll_radar_bot_once(
        token=token,
        allowed_users=allowed,
        add_channel=add_channel,
        offset_get=offset_get,
        offset_set=offset_set,
    )


@app.task(name="tasks.radar_tasks.poll_radar_bot")
def poll_radar_bot():
    """Intake-бот «Карман»: приём форвардов каналов (каждую минуту).

    Гейт #008: no-op, пока не заданы RADAR_BOT_NAME + токен. Форвард поста канала
    боту → канал добавляется в радар + подписка оператора (RADAR_BOT_RADAR_USER_ID).
    """
    try:
        result = run_coro(_run_radar_bot())
        if not result.get("skipped"):
            logger.info("radar intake-bot: %s", result)
        return {"success": True, "timestamp": datetime.now().isoformat(), **result}
    except Exception as e:
        logger.error(f"poll_radar_bot failed: {e}", exc_info=True)
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
