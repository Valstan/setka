"""Celery-таски модуля «Раскрутка» (заказ владельца 2026-08-28).

Этап 0 — только чтение и БД: состав раскрутки и размеры донорских сообществ.
Ни одной публикации в VK здесь нет и на этом этапе быть не должно: сперва
владелец неделю смотрит в разделе, кого модуль считает слабым, кого сильным и
какие пары предлагает, и только потом включаются каналы, которые пишут.

Обе таски возвращают dict и никогда не бросают наружу — беат-цепочку не роняем
(конвенция проекта), а отказ виден в статусе и в логе.
"""

from __future__ import annotations

import logging
from datetime import datetime

from tasks.celery_app import app
from utils.celery_asyncio import run_coro

logger = logging.getLogger(__name__)


@app.task(name="tasks.promo_tasks.sync_promo_enrollments")
def sync_promo_enrollments():
    """Пересобрать состав раскрутки и дозаполнить локальные хэштеги (04:08 MSK).

    Идёт сразу после суточного снимка подписчиков (04:00), чтобы зачисление
    считалось по свежим числам, а не по вчерашним.

    Работает даже при выключенном ``PROMO_DISABLED``: это чтение и запись в свои
    таблицы, ничего наружу не уходит. Kill-switch гасит публикующие каналы, а не
    наблюдение — иначе выключенный модуль переставал бы показывать, что происходит.
    """
    try:
        from database.connection import AsyncSessionLocal
        from modules.promotion.enrollment_service import sync_enrollments

        async def _run():
            async with AsyncSessionLocal() as session:
                return await sync_enrollments(session)

        result = run_coro(_run())
        logger.info("promo enrollments: %s", result)
        return {"success": True, "timestamp": datetime.now().isoformat(), **result}
    except Exception as e:
        logger.error(f"sync_promo_enrollments failed: {e}", exc_info=True)
        return {"success": False, "timestamp": datetime.now().isoformat(), "error": str(e)}


@app.task(name="tasks.promo_tasks.dispatch_promo")
def dispatch_promo():
    """Тик диспетчера раскрутки (по будним часам, минута :08).

    На этапе 1 все каналы в сухом прогоне: задача подбирает пары, собирает
    готовый текст и кладёт его в ``promo_actions`` со статусом ``dry_run``,
    не отправляя ничего. Владелец неделю читает в разделе ровно тот текст,
    который ушёл бы на стену.

    Публикация возможна, только когда сойдутся три условия: снят
    ``PROMO_DISABLED``, модуль не на паузе после ответа ВК, и у канала снят
    ``dry_run``. Любое из трёх держит тишину.
    """
    try:
        from database.connection import AsyncSessionLocal
        from modules.promotion.dispatcher import run_promo_dispatch

        async def _run():
            async with AsyncSessionLocal() as session:
                return await run_promo_dispatch(session)

        result = run_coro(_run())
        if result.get("planned") or result.get("errors"):
            logger.info("promo dispatch: %s", result)
        return {"success": True, "timestamp": datetime.now().isoformat(), **result}
    except Exception as e:
        logger.error(f"dispatch_promo failed: {e}", exc_info=True)
        return {"success": False, "timestamp": datetime.now().isoformat(), "error": str(e)}


@app.task(name="tasks.promo_tasks.check_promo_heartbeat")
def check_promo_heartbeat():
    """Watchdog: алёрт, если боевой модуль давно ничего не публиковал.

    Молчит, пока каналы в сухом прогоне или продвигать некого: сторож, орущий
    на штатную тишину, обучает себя игнорировать — и промолчит тогда, когда
    сломается по-настоящему.
    """
    try:
        from config.runtime import TELEGRAM_ALERT_CHAT_ID, TELEGRAM_TOKENS
        from database.connection import AsyncSessionLocal
        from modules.promotion.dispatcher import maybe_alert_stale_promo

        token = TELEGRAM_TOKENS.get("VALSTANBOT") or TELEGRAM_TOKENS.get("ALERT")

        async def _run():
            async with AsyncSessionLocal() as session:
                return await maybe_alert_stale_promo(
                    session, telegram_token=token, chat_id=TELEGRAM_ALERT_CHAT_ID
                )

        status = run_coro(_run())
        logger.info("promo watchdog: %s", status)
        return {"success": True, "status": status, "timestamp": datetime.now().isoformat()}
    except Exception as e:
        logger.error(f"check_promo_heartbeat failed: {e}", exc_info=True)
        return {"success": False, "timestamp": datetime.now().isoformat(), "error": str(e)}


@app.task(name="tasks.promo_tasks.refresh_promo_community_members")
def refresh_promo_community_members():
    """Обновить размеры донорских сообществ (еженедельно, вторник 05:38 MSK).

    Четыре вызова ``groups.getById`` на всю сеть (500 id за вызов) — сырьё для
    ранжирования кандидатов ручного аутрича и для честного ответа «сколько людей
    в районе вообще можно достать». Только чтение.
    """
    try:
        from database.connection import AsyncSessionLocal
        from modules.promotion.members_refresh import refresh_community_members

        async def _run():
            async with AsyncSessionLocal() as session:
                return await refresh_community_members(session)

        result = run_coro(_run())
        logger.info("promo members: %s", result)
        return {"success": True, "timestamp": datetime.now().isoformat(), **result}
    except Exception as e:
        logger.error(f"refresh_promo_community_members failed: {e}", exc_info=True)
        return {"success": False, "timestamp": datetime.now().isoformat(), "error": str(e)}
