"""
Сетевой «хаб» SETKA: чтение одной группы-источника и раскладка по региональным стенам.

Не использует RegionConfig для псевдо-региона `copy` — всё задаётся через env (секреты на сервере).
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional, Set

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


def _parse_int_list(raw: Optional[str]) -> Optional[Set[str]]:
    if not raw or not raw.strip():
        return None
    return {x.strip().lower() for x in raw.split(",") if x.strip()}


async def execute_copy_setka_network(
    session: AsyncSession,
    *,
    test_mode: bool = False,
) -> Dict[str, Any]:
    """
    1) wall.get у группы-источника (несколько последних записей).
    2) Берём самую свежую запись, которой ещё нет в WorkTable lip и которая не старше порога.
    3) Для каждого активного региона (кроме copy) с vk_group_id — repost или копия текста+вложений на главную стену региона.
    """
    from config.runtime import (
        get_copy_setka_max_post_age_hours,
        get_copy_setka_repost_message,
        get_copy_setka_source_owner_id,
        get_copy_setka_target_region_codes,
        get_parse_tokens,
        copy_setka_use_repost,
    )
    from database.models import Region
    from database.models_extended import WorkTable
    from modules.publisher.vk_publisher_extended import VKPublisher
    from modules.vk_monitor.vk_client import VKClient
    from utils.post_utils import lip_of_post
    from utils.vk_attachments import build_attachments_list, extract_vk_attachments

    source_owner_id = get_copy_setka_source_owner_id()
    if source_owner_id is None:
        logger.warning(
            "COPY_SETKA_SOURCE_GROUP_ID не задан — сетевое копирование отключено. "
            "Задайте в /etc/setka/setka.env отрицательный ID группы-источника."
        )
        return {
            "success": False,
            "skipped": True,
            "error": "COPY_SETKA_SOURCE_GROUP_ID not configured",
            "stats": _empty_stats(),
        }

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
        vk.get_wall_posts, source_owner_id, 15, 0
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
        if now_ts - post_date > max_age:
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

    use_repost = copy_setka_use_repost()
    msg_suffix = get_copy_setka_repost_message()
    publisher = VKPublisher(test_polygon_mode=test_mode)

    successes = 0
    errors: List[str] = []
    for reg in regions:
        gid = int(reg.vk_group_id)
        try:
            if use_repost:
                out = await publisher.publish_repost(
                    group_id=gid,
                    source_owner_id=src_oid,
                    source_post_id=src_pid,
                    message=msg_suffix,
                )
            else:
                full = await asyncio.to_thread(vk.get_post_by_id, src_oid, src_pid)
                if not full:
                    errors.append(f"{reg.code}: get_post_by_id failed")
                    continue
                text = full.get("text") or ""
                att = extract_vk_attachments(full)
                att_list = build_attachments_list(att, max_items=10)
                out = await publisher.publish_digest(
                    group_id=gid,
                    text=text,
                    attachments=att_list,
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
        lip_list = list(known)
        lip_list.append(src_lip)
        if len(lip_list) > 200:
            lip_list = lip_list[-200:]
        wt.lip = lip_list
        await session.commit()

    return {
        "success": successes > 0,
        "posts_published": successes,
        "source_lip": src_lip,
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
