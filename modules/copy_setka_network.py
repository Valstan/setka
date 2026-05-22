"""
Сетевой «хаб» SETKA: группа copy_by_setka → главные стены регионов.

Правила (текст поста на стене источника — главное поле text):
- Если в text есть слово «репост» (без учёта регистра) — на региональные стены
  уходит VK wall.repost прикреплённого поста (copy_history[0] или attachment type=wall).
- Иначе — копия содержимого: при repost-цепочке (copy_history) берётся исходный пост
  целиком (текст + вложения); иначе — сам пост. Публикация wall.post по регионам.

За один запуск обрабатывается не больше одного нового поста; wall.get — последние 10;
история дублей (lip) — не больше 10 идентификаторов.
"""

from __future__ import annotations

import asyncio
import copy as copy_lib
import logging
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

WALL_FETCH_COUNT = 10
LIP_HISTORY_MAX = 10


def _text_has_repost_keyword(text: str) -> bool:
    return "репост" in (text or "").lower()


def _resolve_repost_target(post: Dict[str, Any]) -> Optional[Tuple[int, int]]:
    """Кого репостить: внутренний пост из copy_history или wall-вложение."""
    ch = post.get("copy_history") or []
    if ch:
        o = ch[0]
        try:
            return int(o["owner_id"]), int(o["id"])
        except (KeyError, TypeError, ValueError):
            pass
    for att in post.get("attachments") or []:
        if att.get("type") != "wall":
            continue
        w = att.get("wall") or {}
        oid = w.get("from_id", w.get("owner_id"))
        pid = w.get("id")
        if oid is not None and pid is not None:
            try:
                return int(oid), int(pid)
            except (TypeError, ValueError):
                continue
    return None


async def execute_copy_setka_network(
    session: AsyncSession,
    *,
    test_mode: bool = False,
) -> Dict[str, Any]:
    from config.runtime import (
        copy_setka_disabled,
        get_copy_setka_max_post_age_hours,
        get_copy_setka_repost_message,
        get_copy_setka_source_owner_id,
        get_copy_setka_target_region_codes,
        get_parse_tokens,
    )
    from database.models import Region
    from database.models_extended import WorkTable
    from modules.publisher.vk_publisher_extended import VKPublisher
    from modules.vk_monitor.vk_client import VKClient
    from utils.post_utils import clear_copy_history, lip_of_post
    from utils.vk_attachments import build_attachments_list, extract_vk_attachments

    if copy_setka_disabled():
        logger.info("COPY_SETKA_DISABLED — сетевой хаб пропущен")
        return {
            "success": False,
            "skipped": True,
            "error": "COPY_SETKA_DISABLED",
            "stats": _empty_stats(),
        }

    source_owner_id = get_copy_setka_source_owner_id()

    parse_tokens = get_parse_tokens()
    if not parse_tokens:
        return {"success": False, "error": "No VK tokens configured", "stats": _empty_stats()}

    parse_token = next(iter(parse_tokens.values()))
    vk = VKClient(parse_token)

    result = await session.execute(
        select(WorkTable).where(
            WorkTable.region_code == "copy",
            WorkTable.theme == "setka",
        )
    )
    wt = result.scalars().first()
    if not wt:
        wt = WorkTable(region_code="copy", theme="setka", lip=[], hash=[])
        session.add(wt)
        await session.commit()
        await session.refresh(wt)

    known: Set[str] = set(wt.lip or [])

    posts: List[Dict[str, Any]] = await asyncio.to_thread(
        vk.get_wall_posts, source_owner_id, WALL_FETCH_COUNT, 0
    )
    if not posts:
        return {
            "success": True,
            "message": "no posts on source wall",
            "posts_published": 0,
            "stats": _empty_stats(),
        }

    max_age = int(get_copy_setka_max_post_age_hours() * 3600)
    now_ts = int(time.time())
    candidate: Optional[Dict[str, Any]] = None

    posts_sorted = sorted(posts, key=lambda p: p.get("date", 0), reverse=True)
    for p in posts_sorted:
        oid = p.get("owner_id", source_owner_id)
        pid = p.get("id")
        if pid is None:
            continue
        lip = lip_of_post(int(oid), int(pid))
        if lip in known:
            continue
        post_date = int(p.get("date") or 0)
        if max_age > 0 and now_ts - post_date > max_age:
            continue
        candidate = p
        break

    if candidate is None:
        return {
            "success": True,
            "message": "no fresh post to propagate",
            "posts_published": 0,
            "stats": _empty_stats(),
        }

    src_oid = int(candidate.get("owner_id", source_owner_id))
    src_pid = int(candidate["id"])
    src_lip = lip_of_post(src_oid, src_pid)
    body_text = candidate.get("text") or ""
    use_api_repost = _text_has_repost_keyword(body_text)

    region_filter = get_copy_setka_target_region_codes()
    rq = select(Region).where(
        Region.is_active.is_(True),
        Region.vk_group_id.isnot(None),
        Region.code != "copy",
    )
    if region_filter:
        rq = rq.where(Region.code.in_(list(region_filter)))

    regions_result = await session.execute(rq)
    regions = list(regions_result.scalars().all())
    if not regions:
        return {
            "success": False,
            "error": "no target regions with vk_group_id",
            "stats": _empty_stats(),
        }

    msg_suffix = get_copy_setka_repost_message()

    # Подгрузим community-токены: copy_setka льёт пост в стены наших регионов,
    # значит выгодно использовать community-токен каждой целевой группы.
    from modules.vk_token_router import load_community_tokens

    community_tokens = await load_community_tokens(session)
    publisher = VKPublisher(test_polygon_mode=test_mode, community_tokens=community_tokens)

    repost_pair: Optional[Tuple[int, int]] = None
    copy_text: str = ""
    copy_attachments: List[str] = []

    if use_api_repost:
        repost_pair = _resolve_repost_target(candidate)
        if repost_pair is None:
            logger.warning(
                "copy-setka: в тексте есть «репост», но не найден вложенный пост (copy_history/wall)"
            )
            return {
                "success": False,
                "error": "repost keyword but no inner wall post to repost",
                "source_lip": src_lip,
                "stats": _empty_stats(),
            }
    else:
        raw = copy_lib.deepcopy(candidate)
        effective = clear_copy_history(raw)
        copy_text = effective.get("text") or ""
        att_dict = extract_vk_attachments(effective)
        copy_attachments = build_attachments_list(att_dict, max_items=10)

    successes = 0
    errors: List[str] = []
    for reg in regions:
        gid = int(reg.vk_group_id)
        try:
            if use_api_repost and repost_pair:
                ro, rp = repost_pair
                out = await publisher.publish_repost(
                    group_id=gid,
                    source_owner_id=ro,
                    source_post_id=rp,
                    message=msg_suffix,
                )
            else:
                out = await publisher.publish_digest(
                    group_id=gid,
                    text=copy_text,
                    attachments=copy_attachments,
                )
            if out.get("success"):
                successes += 1
                logger.info("copy-setka: %s -> %s OK %s", src_lip, reg.code, out.get("url"))
            else:
                errors.append(f"{reg.code}: {out.get('error', 'unknown')}")
        except Exception as e:
            logger.exception("copy-setka: failed for %s", reg.code)
            errors.append(f"{reg.code}: {e}")

    if successes > 0:
        prev = list(wt.lip or [])
        prev.append(src_lip)
        wt.lip = prev[-LIP_HISTORY_MAX:]
        await session.commit()

    return {
        "success": successes > 0,
        "posts_published": successes,
        "source_lip": src_lip,
        "mode": "wall.repost" if use_api_repost else "wall.post copy",
        "targets": len(regions),
        "errors": errors[:20],
        "stats": {
            "total_groups_checked": len(regions),
            "total_posts_scanned": len(posts),
            "posts_final_count": 1 if successes else 0,
            "posts_filtered_old": 0,
            "posts_filtered_duplicate_lip": 0,
            "posts_filtered_duplicate_text": 0,
            "posts_filtered_duplicate_foto": 0,
            "posts_filtered_black_id": 0,
            "posts_filtered_no_region_words": 0,
            "posts_filtered_advertisement": 0,
            "posts_filtered_no_attachments": 0,
        },
    }


def _empty_stats() -> Dict[str, int]:
    return {
        "total_groups_checked": 0,
        "total_posts_scanned": 0,
        "posts_filtered_old": 0,
        "posts_filtered_duplicate_lip": 0,
        "posts_filtered_duplicate_text": 0,
        "posts_filtered_duplicate_foto": 0,
        "posts_filtered_black_id": 0,
        "posts_filtered_no_region_words": 0,
        "posts_filtered_advertisement": 0,
        "posts_filtered_no_attachments": 0,
        "posts_final_count": 0,
    }
