"""Единый роутер входящих ЛС сообщества (Этап 1 — багфикс потери сообщений).

Источник — входящие диалоги сообщества (``VKDialogsChecker.fetch_inbound_dialogs``).
Каждое последнее входящее сообщение классифицируется ``classifier.classify`` и
**persist'ится в ``ad_requests`` ДО решения о маршруте** (R1 — ничего не теряем):

- реклама → ``route='ad_cabinet'`` (видна в `/ad-cabinet`);
- не реклама → ``route='notifications'`` (видна в разделе «Уведомления»).

Раньше не-рекламные ЛС не сохранялись вообще и существовали только как VK-флаг
unread; как только он гас (скан читал диалоги community-токеном / оператор открывал
VK), сообщение исчезало из нашего вида. Теперь у каждого ЛС есть собственная строка
со своим статусом обработки (``handling_status``), не зависящим от VK read/unread (R2).

Дедуп — частичный уникальный индекс ``(community_vk_id, peer_id) WHERE
origin='inbound_dm'`` (одна строка на диалог). При НОВОМ входящем (больший
``last_message_id``) строка обновляется и **переоткрывается** (``handling_status``
done→new) — follow-up от уже-обработанного человека не теряется (тот же класс бага).

Отличия от предложки:
- ``vk_post_id`` = NULL (поста нет), ``last_message_id`` = id последнего ЛС;
- ``can_message`` сразу ``True`` для не-групповых авторов: раз пользователь
  написал сообществу, ответить в этот диалог VK всегда разрешает — лишний
  ``isMessagesFromGroupAllowed`` на ``/send`` не нужен.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Callable, Dict, List

from sqlalchemy.dialects.postgresql import insert as pg_insert

from database.models import AdRequest
from modules.ad_cabinet.classifier import classify

logger = logging.getLogger(__name__)


async def _insert_dm_if_new(
    session,
    region: Dict[str, Any],
    dialog: Dict[str, Any],
    is_ad: bool,
    score: int,
    reasons: List[str],
) -> bool:
    """UPSERT ЛС-строки. True если вставлена новая ИЛИ переоткрыта свежим сообщением.

    ``route`` — куда направить: ``ad_cabinet`` (реклама) или ``notifications`` (не
    реклама). При конфликте (диалог уже есть) обновляем снимок и **переоткрываем**
    (``handling_status`` → new) только если пришло НОВОЕ сообщение (больший
    ``last_message_id``) — иначе повторный скан идемпотентен (rowcount 0). ``route``
    при конфликте НЕ трогаем: маршрутом владеет оператор (кнопки R3).

    ``can_message`` для не-групповых авторов ставится ``True`` сразу: пользователь
    написал первым → ответ в диалог разрешён без отдельного VK-пречека.
    """
    now = datetime.utcnow()
    author_is_group = bool(dialog.get("author_is_group"))
    route = "ad_cabinet" if is_ad else "notifications"
    insert_stmt = pg_insert(AdRequest).values(
        origin="inbound_dm",
        region_id=region.get("region_id"),
        community_vk_id=dialog["community_vk_id"],
        community_name=region.get("region_name"),
        vk_post_id=None,
        last_message_id=dialog.get("last_message_id"),
        author_vk_id=dialog.get("author_vk_id"),
        signer_id=None,
        peer_id=dialog.get("peer_id"),
        author_name=dialog.get("author_name"),
        author_is_group=author_is_group,
        text_snapshot=dialog.get("text", ""),
        attachments_json=dialog.get("attachments"),
        photo_urls_json=dialog.get("photo_urls"),
        score=score,
        reasons_json=reasons,
        status="new",
        route=route,
        handling_status="new",
        can_message=None if author_is_group else True,
        can_message_checked_at=None if author_is_group else now,
    )
    excluded = insert_stmt.excluded
    stmt = insert_stmt.on_conflict_do_update(
        index_elements=["community_vk_id", "peer_id"],
        index_where=AdRequest.origin == "inbound_dm",
        set_={
            "last_message_id": excluded.last_message_id,
            "text_snapshot": excluded.text_snapshot,
            "attachments_json": excluded.attachments_json,
            "photo_urls_json": excluded.photo_urls_json,
            "author_name": excluded.author_name,
            "score": excluded.score,
            "reasons_json": excluded.reasons_json,
            # Новое входящее → снова требует внимания (follow-up не теряем).
            "handling_status": "new",
            "handled_at": None,
            "updated_at": now,
        },
        where=(
            AdRequest.last_message_id.is_(None)
            | (excluded.last_message_id > AdRequest.last_message_id)
        ),
    )
    result = await session.execute(stmt)
    return bool(result.rowcount or 0)


async def scan_region_dialogs(
    session,
    checker,
    region: Dict[str, Any],
    classify_fn: Callable = classify,
) -> Dict[str, Any]:
    """Просканировать входящие ЛС одной группы. Возвращает статистику.

    R1: persist'им КАЖДЫЙ диалог (реклама → кабинет, не реклама → уведомления) —
    решение о пропуске больше не теряет сообщение. ``new`` — сколько строк вставлено
    или переоткрыто свежим входящим; ``ads`` — сколько из просканированных реклама.
    """
    dialogs = checker.fetch_inbound_dialogs(region["vk_group_id"])
    ad_count = 0
    new_count = 0
    new_ads = 0
    for dialog in dialogs:
        is_ad, score, reasons = await classify_fn(dialog)
        if is_ad:
            ad_count += 1
        if await _insert_dm_if_new(session, region, dialog, is_ad, score, reasons):
            new_count += 1
            if is_ad:
                new_ads += 1
    await session.commit()
    return {
        "region_code": region.get("region_code"),
        "scanned": len(dialogs),
        "ads": ad_count,
        "new": new_count,
        "new_ads": new_ads,  # из них реклама → для Telegram-алерта (не считаем не-рекламу)
    }


async def run_dm_scan() -> Dict[str, Any]:
    """Top-level скан входящих ЛС всех регионов. Возвращает сводку + new_total."""
    from sqlalchemy import select

    from database.connection import AsyncSessionLocal
    from database.models import Region
    from modules.notifications.vk_dialogs_checker import VKDialogsChecker
    from modules.vk_token_router import load_vk_routing

    user_token, community_tokens = await load_vk_routing()
    if not user_token:
        logger.error("ad_cabinet DM scan: нет годного user-токена")
        return {"success": False, "error": "no user token", "regions": [], "new_total": 0}

    async with AsyncSessionLocal() as session:
        regions = list(
            (await session.execute(select(Region).where(Region.vk_group_id.isnot(None)))).scalars()
        )
        checker = VKDialogsChecker(user_token, community_tokens=community_tokens)

        results: List[Dict[str, Any]] = []
        new_total = 0
        new_ads_total = 0
        for r in regions:
            region = {
                "region_id": r.id,
                "region_name": r.name,
                "region_code": r.code,
                "vk_group_id": r.vk_group_id,
            }
            try:
                stats = await scan_region_dialogs(session, checker, region)
            except Exception as e:
                logger.warning("DM scan failed for %s: %s", r.code, e)
                stats = {
                    "region_code": r.code,
                    "scanned": 0,
                    "ads": 0,
                    "new": 0,
                    "error": str(e),
                }
            results.append(stats)
            new_total += stats.get("new", 0)
            new_ads_total += stats.get("new_ads", 0)

    logger.info(
        "ad_cabinet DM scan: %d new/reopened inbound-DM rows (%d ad → cabinet, "
        "%d non-ad → notifications)",
        new_total,
        new_ads_total,
        new_total - new_ads_total,
    )
    return {
        "success": True,
        "regions": results,
        "new_total": new_total,
        "new_ads_total": new_ads_total,
    }
