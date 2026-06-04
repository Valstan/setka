"""Поллер входящих ЛС рекламного кабинета (блок A).

Аналог ``scanner.py`` (предложка), но источник — входящие диалоги сообщества
(``VKDialogsChecker.fetch_inbound_dialogs``). Каждое последнее входящее сообщение
классифицируется тем же ``classifier.classify`` и, если это реклама, складывается
в ``ad_requests`` с ``origin='inbound_dm'`` через INSERT ... ON CONFLICT DO NOTHING
(дедуп по частичному уникальному индексу ``(community_vk_id, peer_id) WHERE
origin='inbound_dm'`` — одна заявка на диалог).

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
    score: int,
    reasons: List[str],
) -> bool:
    """INSERT ЛС-заявки с ON CONFLICT DO NOTHING. True если вставлена новая строка.

    ``can_message`` для не-групповых авторов ставится ``True`` сразу: пользователь
    написал первым → ответ в диалог разрешён без отдельного VK-пречека.
    """
    now = datetime.utcnow()
    author_is_group = bool(dialog.get("author_is_group"))
    stmt = (
        pg_insert(AdRequest)
        .values(
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
            can_message=None if author_is_group else True,
            can_message_checked_at=None if author_is_group else now,
        )
        .on_conflict_do_nothing(
            index_elements=["community_vk_id", "peer_id"],
            index_where=AdRequest.origin == "inbound_dm",
        )
    )
    result = await session.execute(stmt)
    return bool(result.rowcount or 0)


async def scan_region_dialogs(
    session,
    checker,
    region: Dict[str, Any],
    classify_fn: Callable = classify,
) -> Dict[str, Any]:
    """Просканировать входящие ЛС одной группы. Возвращает статистику."""
    dialogs = checker.fetch_inbound_dialogs(region["vk_group_id"])
    ad_count = 0
    new_count = 0
    for dialog in dialogs:
        is_ad, score, reasons = await classify_fn(dialog)
        if not is_ad:
            continue
        ad_count += 1
        if await _insert_dm_if_new(session, region, dialog, score, reasons):
            new_count += 1
    await session.commit()
    return {
        "region_code": region.get("region_code"),
        "scanned": len(dialogs),
        "ads": ad_count,
        "new": new_count,
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

    logger.info("ad_cabinet DM scan: %d new inbound-DM ad requests", new_total)
    return {"success": True, "regions": results, "new_total": new_total}
