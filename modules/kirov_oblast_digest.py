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
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

DEFAULT_REGION_CODE = "kirov_obl"
THEME_OBLAST = "oblast"


def _defaults_dict(region_config: Any) -> Dict[str, Any]:
    df = getattr(region_config, "digest_filters", None) or {}
    if not isinstance(df, dict):
        return {}
    d = df.get("defaults") or {}
    return d if isinstance(d, dict) else {}


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
    from utils.vk_wall_links import extract_wall_post_refs_from_text
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

    ddef = _defaults_dict(region_config)
    wall_depth = int(ddef.get("oblast_wall_posts_per_source", 15))
    wall_depth = max(1, min(wall_depth, 100))
    max_refs = int(ddef.get("oblast_max_wall_refs", 200))
    max_refs = max(10, min(max_refs, 500))

    source_codes = await _resolve_source_region_codes(session, region_code, region_config)
    if not source_codes:
        return {"success": False, "error": "Нет районов-источников (oblast_source_region_codes или активные регионы)"}

    parse_tokens = get_parse_tokens()
    if not parse_tokens:
        return {"success": False, "error": "No VK tokens configured"}
    parse_token = next(iter(parse_tokens.values()))
    vk = VKClient(parse_token)

    refs_ordered: List[Tuple[int, int]] = []
    seen: Set[Tuple[int, int]] = set()

    for code in source_codes:
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
            txt = (wp.get("text") or "") + "\n"
            for oid, pid in extract_wall_post_refs_from_text(txt):
                key = (oid, pid)
                if key in seen:
                    continue
                seen.add(key)
                refs_ordered.append(key)
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
        }

    raw_posts = await asyncio.to_thread(vk.get_posts_by_ids, refs_ordered)
    if not raw_posts:
        return {"success": False, "error": "wall.getById returned no posts"}

    pipeline_eff = get_effective_pipeline_settings(region_config, theme)
    parser = AdvancedVKParser(vk)
    posts = await parser.filter_posts_list(
        raw_posts,
        theme=theme,
        region_config=region_config,
        work_table_lip=work_table.lip or [],
        work_table_hash=work_table.hash or [],
        recent_text_fingerprints=[],
        pipeline_settings=pipeline_eff,
    )
    parser_stats = parser.get_stats()

    comm_meta = await session.execute(
        select(Community.vk_id, Community.name).where(Community.is_active == True)
    )
    group_names = {str(abs(row[0])): row[1] for row in comm_meta.fetchall()}

    splitter = DigestSplitter()
    mourning_posts, regular_posts = splitter.split_posts(posts)

    header = resolve_digest_header(region_config, theme, region)
    theme_tags, local_hashtag = resolve_digest_hashtags(region_config, theme)

    results = []
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
        lip = work_table.lip or []
        lip.extend(all_included)
        if len(lip) > 30:
            lip = lip[-30:]
        work_table.lip = lip
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
    }
