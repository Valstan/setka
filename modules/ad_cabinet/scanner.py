"""Поллер предложки рекламного кабинета.

Для каждого региона с главной группой: читает предложку
(``VKSuggestedChecker.fetch_suggested_posts``), классифицирует каждый пост
(``classifier.classify``) и складывает рекламные заявки в ``ad_requests``
через INSERT ... ON CONFLICT DO NOTHING (дедуп по ``(community_vk_id,
vk_post_id)``; уже существующие строки — в т.ч. ``contacted`` — не трогаются).

Жизненный цикл заявок переживает рескан, когда предложенный пост уже
опубликован/удалён — поэтому Postgres, а не Redis-снимки notifications.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from database.models import AdRequest
from modules.ad_cabinet.classifier import classify

logger = logging.getLogger(__name__)


async def _precheck_can_message(
    post: Dict[str, Any],
    user_token: Optional[str],
    community_tokens: Optional[Dict[int, str]],
) -> Optional[bool]:
    """Может ли группа писать автору заявки (precheck на этапе скана).

    Снимает VK-вызов с момента «оператор жмёт Отправить»: результат
    ``messages.isMessagesFromGroupAllowed`` кэшируется в заявку сразу при
    обнаружении. Возвращает None, если precheck неприменим (автор — группа /
    нерезолвимый peer / нет токена) или VK-вызов упал — тогда решение
    останется на момент ``/send`` (как раньше). Вызов VK синхронный — уносим
    в поток, чтобы не блокировать event loop.
    """
    if not user_token:
        return None
    if post.get("author_is_group"):
        return None
    peer_id = post.get("peer_id")
    if not peer_id or int(peer_id) <= 0:
        return None
    try:
        from modules.notifications.vk_actions import messages_allowed

        return await asyncio.to_thread(
            messages_allowed,
            group_id=int(post["community_vk_id"]),
            user_id=int(peer_id),
            user_token=user_token,
            community_tokens=community_tokens or {},
        )
    except Exception as e:  # pragma: no cover - VK/network flake → решаем на /send
        logger.debug("can_message precheck failed: %s", e)
        return None


async def _insert_if_new(
    session,
    region: Dict[str, Any],
    parsed: Dict[str, Any],
    score: int,
    reasons: List[str],
) -> bool:
    """INSERT заявки с ON CONFLICT DO NOTHING. True если вставлена новая строка."""
    stmt = (
        pg_insert(AdRequest)
        .values(
            region_id=region.get("region_id"),
            community_vk_id=parsed["community_vk_id"],
            community_name=region.get("region_name"),
            vk_post_id=parsed["vk_post_id"],
            author_vk_id=parsed.get("author_vk_id"),
            signer_id=parsed.get("signer_id"),
            peer_id=parsed.get("peer_id"),
            author_name=parsed.get("author_name"),
            author_is_group=parsed.get("author_is_group", False),
            text_snapshot=parsed.get("text", ""),
            attachments_json=parsed.get("attachments"),
            photo_urls_json=parsed.get("photo_urls"),
            score=score,
            reasons_json=reasons,
            status="new",
        )
        .on_conflict_do_nothing(index_elements=["community_vk_id", "vk_post_id"])
    )
    result = await session.execute(stmt)
    return bool(result.rowcount or 0)


async def scan_region_group(
    session,
    checker,
    region: Dict[str, Any],
    classify_fn: Callable = classify,
    *,
    user_token: Optional[str] = None,
    community_tokens: Optional[Dict[int, str]] = None,
) -> Dict[str, Any]:
    """Просканировать предложку одной группы. Возвращает статистику.

    Если переданы токены — для КАЖДОЙ НОВОЙ заявки сразу прокачивается
    ``can_message`` (precheck), чтобы оператору не ждать VK-вызов при отправке.
    Precheck делается только для новых строк (rowcount>0) — рескан уже
    известных заявок лишних VK-вызовов не порождает.
    """
    posts = checker.fetch_suggested_posts(region["vk_group_id"])
    ad_count = 0
    new_count = 0
    for post in posts:
        is_ad, score, reasons = await classify_fn(post)
        if not is_ad:
            continue
        ad_count += 1
        if await _insert_if_new(session, region, post, score, reasons):
            new_count += 1
            can_msg = await _precheck_can_message(post, user_token, community_tokens)
            if can_msg is not None:
                await session.execute(
                    update(AdRequest)
                    .where(
                        AdRequest.community_vk_id == post["community_vk_id"],
                        AdRequest.vk_post_id == post["vk_post_id"],
                    )
                    .values(can_message=can_msg, can_message_checked_at=datetime.utcnow())
                )
    await session.commit()
    return {
        "region_code": region.get("region_code"),
        "scanned": len(posts),
        "ads": ad_count,
        "new": new_count,
    }


async def run_scan() -> Dict[str, Any]:
    """Top-level скан предложки всех регионов. Возвращает сводку + new_total."""
    from sqlalchemy import select

    from database.connection import AsyncSessionLocal
    from database.models import Region
    from modules.notifications.vk_suggested_checker import VKSuggestedChecker
    from modules.vk_token_router import load_vk_routing

    user_token, community_tokens = await load_vk_routing()
    if not user_token:
        logger.error("ad_cabinet scan: нет годного user-токена")
        return {"success": False, "error": "no user token", "regions": [], "new_total": 0}

    async with AsyncSessionLocal() as session:
        regions = list(
            (await session.execute(select(Region).where(Region.vk_group_id.isnot(None)))).scalars()
        )
        checker = VKSuggestedChecker(user_token, community_tokens=community_tokens)

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
                stats = await scan_region_group(
                    session,
                    checker,
                    region,
                    user_token=user_token,
                    community_tokens=community_tokens,
                )
            except Exception as e:
                logger.warning("ad scan failed for %s: %s", r.code, e)
                stats = {
                    "region_code": r.code,
                    "scanned": 0,
                    "ads": 0,
                    "new": 0,
                    "error": str(e),
                }
            results.append(stats)
            new_total += stats.get("new", 0)

    logger.info("ad_cabinet scan: %d new ad requests across %d regions", new_total, len(results))
    return {"success": True, "regions": results, "new_total": new_total}
