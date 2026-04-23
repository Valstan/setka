"""
Дайджест «Кировская область» (код региона по умолчанию kirov_obl, тема oblast).

Собирает ссылки vk.com/wall из последних постов на стенах районных групп,
загружает исходные посты (источники из дайджестов), фильтрует тем же пайплайном,
что и обычные регионы, и публикует сводку по популярности.

Настройки в RegionConfig.digest_filters.defaults:
- oblast_source_region_codes: ["mi", "nolinsk", ...] — если пусто, берутся все активные
  регионы с vk_group_id, кроме самого kirov_obl.
- oblast_wall_posts_per_source: сколько последних постов стены читать у каждого района (по умолчанию 15).
- oblast_max_wall_refs: максимум ссылок на загрузку за один запуск (по умолчанию 200).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from utils.vk_wall_links import extract_wall_post_refs_from_text

logger = logging.getLogger(__name__)

DEFAULT_REGION_CODE = "kirov_obl"
THEME_OBLAST = "oblast"
WORK_TABLE_LIP_LIMIT = 1000
WORK_TABLE_HASH_LIMIT = 5000
OBLAST_LOOKBACK_HOURS = 72.0
OBLAST_DIGEST_SCAN_DEPTH = 100

_OBLAST_BANNED_DIGEST_MARKERS = (
    "реклама",
    "объявлен",
    "дополнительно",
    "addons",
)

_OBLAST_RELIGIOUS_MARKERS = (
    "православ",
    "церков",
    "храм",
    "молитв",
    "епарх",
    "богослуж",
    "священ",
    "митрополит",
    "монастыр",
    "пасх",
    "крещен",
    "ислам",
    "мечет",
    "намаз",
)


def _defaults_dict(region_config: Any) -> Dict[str, Any]:
    df = getattr(region_config, "digest_filters", None) or {}
    if not isinstance(df, dict):
        return {}
    d = df.get("defaults") or {}
    return d if isinstance(d, dict) else {}


def _post_age_hours_from_vk_date(post_data: Dict[str, Any]) -> Optional[float]:
    raw = post_data.get("date")
    if raw is None:
        return None
    try:
        ts = float(raw)
    except (TypeError, ValueError):
        return None
    now_ts = datetime.now(tz=timezone.utc).timestamp()
    return max(0.0, (now_ts - ts) / 3600.0)


def _is_recent_enough(post_data: Dict[str, Any], max_age_hours: float) -> bool:
    age = _post_age_hours_from_vk_date(post_data)
    if age is None:
        return False
    return age <= max_age_hours


def _is_oblast_source_digest_text(text: str) -> bool:
    """
    Keep only digest-like source posts with wall refs and without obvious banned digest themes.
    """
    txt = (text or "").strip()
    if not txt:
        return False
    refs = extract_wall_post_refs_from_text(txt)
    if not refs:
        return False
    low = txt.lower()
    if any(marker in low for marker in _OBLAST_BANNED_DIGEST_MARKERS):
        return False
    return True


def _is_religious_text(text: str) -> bool:
    low = (text or "").lower()
    return any(marker in low for marker in _OBLAST_RELIGIOUS_MARKERS)


async def _resolve_source_region_codes(
    session: AsyncSession,
    oblast_code: str,
    region_config: Any,
) -> List[str]:
    d = _defaults_dict(region_config)
    raw = d.get("oblast_source_region_codes")
    if isinstance(raw, list) and raw:
        return [str(x).strip() for x in raw if str(x).strip() and str(x).strip() != oblast_code]

    from database.models import Region

    r = await session.execute(
        select(Region.code).where(
            Region.is_active == True,
            Region.vk_group_id.isnot(None),
            Region.code != oblast_code,
        )
    )
    return [row[0] for row in r.fetchall()]


async def run_kirov_oblast_digest(
    session: AsyncSession,
    *,
    region_code: str = DEFAULT_REGION_CODE,
    theme: str = THEME_OBLAST,
    test_mode: bool = False,
) -> Dict[str, Any]:
    from database.models import Community, Region
    from database.models_extended import RegionConfig, WorkTable
    from modules.digest_pipeline_settings import get_effective_pipeline_settings
    from modules.publisher.digest_splitter import DigestSplitter
    from modules.publisher.digest_builder import DigestBuilder
    from modules.publisher.postopus_digest_headers import (
        resolve_digest_header,
        resolve_digest_hashtags,
        resolve_mourning_digest_format,
    )
    from modules.publisher.vk_publisher_extended import VKPublisher
    from modules.vk_monitor.advanced_parser import AdvancedVKParser
    from modules.vk_monitor.vk_client import VKClient
    from config.runtime import get_parse_tokens
    from utils.post_utils import lip_of_post
    from utils.text_utils import is_advertisement
    from modules.deduplication.fingerprints import (
        create_text_fingerprint,
        create_text_core_fingerprint,
        create_text_simhash,
        create_media_fingerprint,
        text_to_rafinad,
    )
    from modules.deduplication.digest_history import (
        GLOBAL_REGION_WORK_THEME,
        TARGET_GROUP_POSTS_SCAN_LIMIT,
        build_region_dedup_sets,
        extract_source_lips_from_target_group_posts,
        append_unique_limited,
    )
    from types import SimpleNamespace

    result = await session.execute(select(Region).where(Region.code == region_code))
    region = result.scalars().first()
    if not region or not region.vk_group_id:
        return {"success": False, "error": f"Region {region_code} missing or no vk_group_id"}

    cfg_result = await session.execute(
        select(RegionConfig).where(RegionConfig.region_code == region_code)
    )
    region_config = cfg_result.scalars().first()
    if not region_config:
        region_config = SimpleNamespace(
            region_code=region_code,
            zagolovki={},
            heshteg={},
            heshteg_local={},
            black_id=[],
            delete_msg_blacklist=[],
            filter_group_by_region_words={},
            text_post_maxsize_simbols=4096,
            setka_regim_repost=False,
            digest_filters=None,
        )

    wt_result = await session.execute(
        select(WorkTable).where(
            WorkTable.region_code == region_code,
            WorkTable.theme == theme,
        )
    )
    work_table = wt_result.scalars().first()
    if not work_table:
        work_table = WorkTable(region_code=region_code, theme=theme, lip=[], hash=[])
        session.add(work_table)
        await session.commit()
        await session.refresh(work_table)

    global_wt_result = await session.execute(
        select(WorkTable).where(
            WorkTable.region_code == region_code,
            WorkTable.theme == GLOBAL_REGION_WORK_THEME,
        )
    )
    global_work_table = global_wt_result.scalars().first()
    if not global_work_table:
        global_work_table = WorkTable(
            region_code=region_code,
            theme=GLOBAL_REGION_WORK_THEME,
            lip=[],
            hash=[],
        )
        session.add(global_work_table)
        await session.commit()
        await session.refresh(global_work_table)

    ddef = _defaults_dict(region_config)
    wall_depth = int(ddef.get("oblast_wall_posts_per_source", OBLAST_DIGEST_SCAN_DEPTH))
    wall_depth = max(1, min(wall_depth, OBLAST_DIGEST_SCAN_DEPTH))
    max_refs = int(ddef.get("oblast_max_wall_refs", 200))
    max_refs = max(10, min(max_refs, 500))

    source_codes = await _resolve_source_region_codes(session, region_code, region_config)
    if not source_codes:
        return {
            "success": True,
            "message": "no source regions configured for oblast digest",
            "posts_published": 0,
            "digests_count": 0,
            "stats": {},
        }

    parse_tokens = get_parse_tokens()
    if not parse_tokens:
        return {"success": False, "error": "No VK tokens configured"}
    parse_token = next(iter(parse_tokens.values()))
    vk = VKClient(parse_token)

    all_wt_result = await session.execute(
        select(WorkTable).where(WorkTable.region_code == region_code)
    )
    region_lips, region_hashes = build_region_dedup_sets(all_wt_result.scalars().all())
    debug_counters: Dict[str, int] = {
        "source_regions_total": len(source_codes),
        "source_regions_scanned": 0,
        "source_digest_posts_scanned": 0,
        "source_digest_posts_old": 0,
        "source_digest_posts_non_digest": 0,
        "source_refs_collected": 0,
        "raw_posts_loaded": 0,
        "filtered_posts_after_pipeline": 0,
        "filtered_posts_non_news_ads": 0,
        "filtered_posts_non_news_religious": 0,
        "filtered_posts_mourning": 0,
        "regular_posts_ready": 0,
        "mourning_posts_ready": 0,
    }
    try:
        target_group_posts = await asyncio.to_thread(
            vk.get_wall_posts, -abs(int(region.vk_group_id)), TARGET_GROUP_POSTS_SCAN_LIMIT, 0
        )
        region_lips.update(extract_source_lips_from_target_group_posts(target_group_posts))
    except Exception as e:
        logger.warning("Kirov oblast: failed to load target group digest history: %s", e)

    refs_ordered: List[Tuple[int, int]] = []
    seen: Set[Tuple[int, int]] = set()

    for code in source_codes:
        debug_counters["source_regions_scanned"] += 1
        rr = await session.execute(select(Region).where(Region.code == code))
        src_region = rr.scalars().first()
        if not src_region or not src_region.vk_group_id:
            continue
        owner = -abs(int(src_region.vk_group_id))
        try:
            wall_posts = await asyncio.to_thread(vk.get_wall_posts, owner, wall_depth, 0)
        except Exception as e:
            logger.warning("Kirov oblast: wall.get failed for %s: %s", code, e)
            continue
        for wp in wall_posts or []:
            debug_counters["source_digest_posts_scanned"] += 1
            if not _is_recent_enough(wp, OBLAST_LOOKBACK_HOURS):
                debug_counters["source_digest_posts_old"] += 1
                continue
            txt = (wp.get("text") or "") + "\n"
            if not _is_oblast_source_digest_text(txt):
                debug_counters["source_digest_posts_non_digest"] += 1
                continue
            for oid, pid in extract_wall_post_refs_from_text(txt):
                key = (oid, pid)
                if key in seen:
                    continue
                seen.add(key)
                refs_ordered.append(key)
                debug_counters["source_refs_collected"] += 1
                if len(refs_ordered) >= max_refs:
                    break
        if len(refs_ordered) >= max_refs:
            break

    if not refs_ordered:
        return {
            "success": True,
            "message": "no wall links found in regional digest posts",
            "posts_published": 0,
            "digests_count": 0,
            "stats": {},
            "debug": debug_counters,
        }

    raw_posts = await asyncio.to_thread(vk.get_posts_by_ids, refs_ordered)
    debug_counters["raw_posts_loaded"] = len(raw_posts or [])
    if not raw_posts:
        return {
            "success": True,
            "message": "wall.getById returned no posts",
            "posts_published": 0,
            "digests_count": 0,
            "stats": {},
            "debug": debug_counters,
        }

    pipeline_eff = get_effective_pipeline_settings(region_config, theme)
    parser = AdvancedVKParser(vk)
    posts = await parser.filter_posts_list(
        raw_posts,
        theme=theme,
        region_config=region_config,
        work_table_lip=list(region_lips),
        work_table_hash=list(region_hashes),
        recent_text_fingerprints=[],
        pipeline_settings=pipeline_eff,
    )
    debug_counters["filtered_posts_after_pipeline"] = len(posts)
    parser_stats = parser.get_stats()

    # Hardcoded oblast-only exclusions by requirement:
    # - remove ad/addons-like posts
    # - remove religious posts
    oblast_news_posts: List[Dict[str, Any]] = []
    for p in posts:
        txt = (p.get("text") or "").strip()
        txt_low = txt.lower()
        if any(marker in txt_low for marker in _OBLAST_BANNED_DIGEST_MARKERS):
            debug_counters["filtered_posts_non_news_ads"] += 1
            continue
        if is_advertisement(txt, skip_for_reklama=False, theme="novost"):
            debug_counters["filtered_posts_non_news_ads"] += 1
            continue
        if _is_religious_text(txt):
            debug_counters["filtered_posts_non_news_religious"] += 1
            continue
        oblast_news_posts.append(p)

    comm_meta = await session.execute(
        select(Community.vk_id, Community.name).where(Community.is_active == True)
    )
    group_names = {str(abs(row[0])): row[1] for row in comm_meta.fetchall()}

    splitter = DigestSplitter()
    mourning_posts, regular_posts = splitter.split_posts(oblast_news_posts)
    debug_counters["mourning_posts_ready"] = len(mourning_posts)
    debug_counters["regular_posts_ready"] = len(regular_posts)
    # Mourning must be excluded for oblast digest.
    if mourning_posts:
        debug_counters["filtered_posts_mourning"] = len(mourning_posts)
    mourning_posts = []

    header = resolve_digest_header(region_config, theme, region)
    theme_tags, local_hashtag = resolve_digest_hashtags(region_config, theme)

    results = []
    selected_by_lip: Dict[str, Dict[str, Any]] = {}
    if regular_posts:
        builder = DigestBuilder(
            header=header,
            hashtags=theme_tags,
            local_hashtag=local_hashtag,
            max_text_length=region_config.text_post_maxsize_simbols or 4096,
            repost_mode=region_config.setka_regim_repost,
            max_posts_per_digest=pipeline_eff.get("max_posts_per_digest"),
        )
        digest = builder.build_digest(regular_posts, group_names=group_names)
        selected_by_lip.update({
            lip_of_post(
                p.get("owner_id", p.get("from_id", 0)),
                p.get("id", 0),
            ): p
            for p in regular_posts
        })
        vk_pub = VKPublisher(test_polygon_mode=test_mode)
        pub = await vk_pub.publish_digest(
            group_id=region.vk_group_id,
            text=digest.text,
            attachments=digest.attachments_list,
        )
        results.append(("regular", digest, pub))

    if mourning_posts:
        mourning_header, mourning_tags, mourning_local_hashtag = resolve_mourning_digest_format()
        mb = DigestBuilder(
            header=mourning_header,
            hashtags=mourning_tags,
            local_hashtag=mourning_local_hashtag,
            max_text_length=region_config.text_post_maxsize_simbols or 4096,
            max_posts_per_digest=pipeline_eff.get("max_posts_per_digest"),
        )
        md = mb.build_digest(mourning_posts, group_names=group_names)
        selected_by_lip.update({
            lip_of_post(
                p.get("owner_id", p.get("from_id", 0)),
                p.get("id", 0),
            ): p
            for p in mourning_posts
        })
        vk_pub2 = VKPublisher(test_polygon_mode=test_mode)
        mp = await vk_pub2.publish_digest(
            group_id=region.vk_group_id,
            text=md.text,
            attachments=md.attachments_list,
        )
        results.append(("mourning", md, mp))

    all_included: List[str] = []
    for _, d, _ in results:
        all_included.extend(d.posts_included)
    if all_included:
        work_table.lip = append_unique_limited(
            work_table.lip or [],
            all_included,
            WORK_TABLE_LIP_LIMIT,
        )
        global_work_table.lip = append_unique_limited(
            global_work_table.lip or [],
            all_included,
            WORK_TABLE_LIP_LIMIT,
        )

        new_hash_entries: List[str] = []
        for included_lip in all_included:
            p = selected_by_lip.get(included_lip)
            if not isinstance(p, dict):
                continue
            text = (p.get("text") or "").strip()
            if text:
                tfp = create_text_fingerprint(text)
                if tfp:
                    new_hash_entries.append(f"txtfp:{tfp}")
                cfp = create_text_core_fingerprint(text)
                if cfp:
                    new_hash_entries.append(f"txtcore:{cfp}")
                rafinad_len = len(text_to_rafinad(text))
                if rafinad_len >= 80:
                    simhash = create_text_simhash(text)
                    if simhash:
                        new_hash_entries.append(f"txtsim:{rafinad_len // 20}:{simhash}")
            atts = p.get("attachments")
            media_ids = create_media_fingerprint(atts if isinstance(atts, list) else [])
            new_hash_entries.extend(media_ids)
        work_table.hash = append_unique_limited(
            work_table.hash or [],
            new_hash_entries,
            WORK_TABLE_HASH_LIMIT,
        )
        global_work_table.hash = append_unique_limited(
            global_work_table.hash or [],
            new_hash_entries,
            WORK_TABLE_HASH_LIMIT,
        )
        await session.commit()

    total_published = sum(d.post_count for _, d, _ in results)
    first_url = results[0][2].get("url") if results else None

    return {
        "success": all(r[2].get("success", False) for r in results) if results else True,
        "posts_published": total_published,
        "published_url": first_url,
        "mourning_posts": len(mourning_posts),
        "regular_posts": len(regular_posts),
        "digests_count": len(results),
        "stats": parser_stats,
        "source_regions_scanned": len(source_codes),
        "wall_links_collected": len(refs_ordered),
        "debug": debug_counters,
    }
