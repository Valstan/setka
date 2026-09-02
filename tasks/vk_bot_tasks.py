"""Celery-задача ВК-бота кабинета: один ручной тик Long Poll (диагностика).

В расписании beat её нет: постоянный опрос ведёт демон ``setka-vk-bot``
(``scripts/vk_bot_daemon.py``), два читателя одного Long Poll делили бы
события. Задача — для ручной проверки: ``celery call tasks.vk_bot_tasks.poll_sarafan_vk_bot``.
No-op без ``SARAFAN_VK_COMMUNITY_ID`` и community-токена.
"""

from __future__ import annotations

import logging
from datetime import datetime

from tasks.celery_app import app
from utils.celery_asyncio import run_coro

logger = logging.getLogger(__name__)


async def _run_vk_bot_tick():
    from config.runtime import get_sarafan_vk_community_id

    group_id = get_sarafan_vk_community_id()
    if not group_id:
        return {"skipped": "SARAFAN_VK_COMMUNITY_ID not set"}

    from modules.vk_token_router import load_vk_routing

    _user_token, community_tokens = await load_vk_routing()
    token = (community_tokens or {}).get(group_id)
    if not token:
        return {"skipped": f"no community token for {group_id}"}

    from modules.bulletin_heartbeat import _redis

    r = _redis()
    if r is None:
        return {"skipped": "no redis (state + ts persistence required)"}

    from database.connection import AsyncSessionLocal
    from modules.ad_cabinet.vk_bot import intake

    def ts_get():
        v = r.get(intake.TS_KEY)
        return None if v is None else str(v)

    def ts_set(v):
        if v is None:
            r.delete(intake.TS_KEY)
        else:
            r.set(intake.TS_KEY, str(v))

    state_get, state_set = intake.redis_state_store(r)
    return await intake.poll_once(
        token=token,
        group_id=group_id,
        session_factory=AsyncSessionLocal,
        state_get=state_get,
        state_set=state_set,
        ts_get=ts_get,
        ts_set=ts_set,
    )


@app.task(name="tasks.vk_bot_tasks.poll_sarafan_vk_bot")
def poll_sarafan_vk_bot():
    """ВК-бот кабинета: приём сообщений сообществу САРАФАН (каждую минуту)."""
    try:
        result = run_coro(_run_vk_bot_tick())
        if not result.get("skipped") and (result.get("processed") or not result.get("ok", True)):
            logger.info("vk_bot tick: %s", result)
        return {"success": True, "timestamp": datetime.now().isoformat(), **result}
    except Exception as e:  # noqa: BLE001
        logger.error("poll_sarafan_vk_bot failed: %s", e, exc_info=True)
        return {"success": False, "error": str(e)}
