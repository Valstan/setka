"""
Celery tasks for Postopus migration - replaces crontab scheduling

Migrated from old_postopus crontab entries to Celery Beat schedule.
Each theme/region combination gets its own scheduled task.
"""

import asyncio
import logging
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Dict, List

from celery import shared_task

from utils.celery_asyncio import run_coro

logger = logging.getLogger(__name__)

WORK_TABLE_LIP_LIMIT = 1000
WORK_TABLE_HASH_LIMIT = 5000


@shared_task(bind=True, max_retries=3)
def parse_and_publish_theme(
    self,
    region_code: str,
    theme: str,
    test_mode: bool = False,
) -> Dict[str, Any]:
    """
    Main parsing and publishing task for a region/theme.
    Celery-compatible: uses run_coro (one event loop per worker process).
    """
    from sqlalchemy import select

    from config.runtime import get_parse_tokens
    from database.connection import AsyncSessionLocal
    from database.models import Community, Region
    from database.models_extended import ParsingStats, RegionConfig, WorkTable
    from modules.deduplication.digest_history import (
        GLOBAL_REGION_WORK_THEME, TARGET_GROUP_POSTS_SCAN_LIMIT,
        append_unique_limited, build_region_dedup_sets,
        extract_source_lips_from_target_group_posts)
    from modules.deduplication.fingerprints import (
        create_media_fingerprint, create_text_core_fingerprint,
        create_text_fingerprint, create_text_simhash, text_to_rafinad)
    from modules.digest_pipeline_settings import \
        get_effective_pipeline_settings
    from modules.publisher.digest_builder import DigestBuilder
    from modules.publisher.digest_splitter import DigestSplitter
    from modules.publisher.postopus_digest_headers import (
        resolve_digest_hashtags, resolve_digest_header,
        resolve_mourning_digest_format)
    from modules.publisher.vk_publisher_extended import VKPublisher
    from modules.vk_monitor.advanced_parser import AdvancedVKParser
    from modules.vk_monitor.vk_client import VKClient
    from utils.post_utils import lip_of_post

    start_time = datetime.now()

    async def _execute():
        """Execute parsing and publishing pipeline."""
        async with AsyncSessionLocal() as session:
            # Псевдо-регион «copy» + тема «setka» — отдельный сетевой хаб
            # (env COPY_SETKA_*), без RegionConfig.
            if region_code == "copy" and theme == "setka":
                from modules.copy_setka_network import \
                    execute_copy_setka_network

                return await execute_copy_setka_network(session, test_mode=test_mode)

            # Кировская область: дайджест из ссылок на источники в постах
            # районных групп (тема oblast).
            if region_code == "kirov_obl" and theme == "oblast":
                from modules.kirov_oblast_digest import run_kirov_oblast_digest

                return await run_kirov_oblast_digest(
                    session,
                    region_code=region_code,
                    theme=theme,
                    test_mode=test_mode,
                )

            # 1. Get region config
            result = await session.execute(
                select(RegionConfig).where(RegionConfig.region_code == region_code)
            )
            region_config = result.scalars().first()
            if not region_config:
                logger.warning(
                    f"RegionConfig not found for {region_code}; using safe defaults"
                )
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

            # 2. Get work table
            result = await session.execute(
                select(WorkTable).where(
                    WorkTable.region_code == region_code, WorkTable.theme == theme
                )
            )
            work_table = result.scalars().first()
            if not work_table:
                work_table = WorkTable(
                    region_code=region_code, theme=theme, lip=[], hash=[]
                )
                session.add(work_table)
                await session.commit()

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

            # 3. Get region
            region_result = await session.execute(
                select(Region).where(Region.code == region_code)
            )
            region = region_result.scalars().first()
            if not region or not region.vk_group_id:
                return {
                    "success": False,
                    "error": f"No VK group ID for region {region_code}",
                }

            # 4. Get communities for this theme
            communities_result = await session.execute(
                select(Community.vk_id).where(
                    Community.region_id == region.id,
                    Community.category == theme,
                    Community.is_active.is_(True),
                )
            )
            community_ids = [row[0] for row in communities_result.fetchall()]

            if not community_ids:
                logger.warning(
                    f"No communities found for {region_code}/{theme}; "
                    "falling back to all active communities in region"
                )
                fallback_result = await session.execute(
                    select(Community.vk_id).where(
                        Community.region_id == region.id, Community.is_active.is_(True)
                    )
                )
                community_ids = [row[0] for row in fallback_result.fetchall()]
            if not community_ids:
                return {"success": False, "error": "No communities found"}

            # Имена сообществ для кликабельных ссылок «источник» в дайджесте
            comm_meta = await session.execute(
                select(Community.vk_id, Community.name).where(
                    Community.region_id == region.id
                )
            )
            group_names = {str(abs(row[0])): row[1] for row in comm_meta.fetchall()}

            # 5. Parse
            parse_tokens = get_parse_tokens()
            parse_token = next(iter(parse_tokens.values())) if parse_tokens else None
            if not parse_token:
                return {"success": False, "error": "No VK tokens configured"}
            vk_client = VKClient(parse_token)
            parser = AdvancedVKParser(vk_client)
            pipeline_eff = get_effective_pipeline_settings(region_config, theme)

            all_wt_result = await session.execute(
                select(WorkTable).where(WorkTable.region_code == region_code)
            )
            region_lips, region_hashes = build_region_dedup_sets(
                all_wt_result.scalars().all()
            )
            try:
                target_group_posts = await asyncio.to_thread(
                    vk_client.get_wall_posts,
                    -abs(int(region.vk_group_id)),
                    TARGET_GROUP_POSTS_SCAN_LIMIT,
                    0,
                )
                region_lips.update(
                    extract_source_lips_from_target_group_posts(target_group_posts)
                )
            except Exception as e:
                logger.warning(
                    "Failed to load target group digest history for %s: %s",
                    region_code,
                    e,
                )

            posts = await parser.parse_posts_from_communities(
                community_ids=community_ids,
                theme=theme,
                region_config=region_config,
                work_table_lip=list(region_lips),
                work_table_hash=list(region_hashes),
                count_per_community=20,
                pipeline_settings=pipeline_eff,
            )
            parser_stats = parser.get_stats()

            # 6. Split by sentiment
            splitter = DigestSplitter()
            mourning_posts, regular_posts = splitter.split_posts(posts)
            logger.info(
                f"Split: {len(mourning_posts)} mourning, {len(regular_posts)} regular"
            )

            # 7. Build digests (заголовки/хештеги как в old_postopus, см. postopus_digest_headers)
            header = resolve_digest_header(region_config, theme, region)
            theme_tags, local_hashtag = resolve_digest_hashtags(region_config, theme)

            # Community access tokens + publish-кандидаты подбираются внутри
            # ``VKPublisher.create_with_policy`` (см. modules.vk_token_router.TokenPolicy).
            results = []
            selected_by_lip: Dict[str, Dict[str, Any]] = {}

            # Regular digest
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
                if digest.post_count == 0 or not digest.text.strip():
                    logger.warning(
                        "Empty regular digest after build, skipping publish "
                        "(region=%s theme=%s candidates=%d)",
                        region.code,
                        theme,
                        len(regular_posts),
                    )
                else:
                    selected_by_lip.update(
                        {
                            lip_of_post(
                                p.get("owner_id", p.get("from_id", 0)),
                                p.get("id", 0),
                            ): p
                            for p in regular_posts
                        }
                    )

                    vk_publisher = await VKPublisher.create_with_policy(
                        session,
                        target_group_id=region.vk_group_id,
                        test_polygon_mode=test_mode,
                    )
                    publish_result = await vk_publisher.publish_digest(
                        group_id=region.vk_group_id,
                        text=digest.text,
                        attachments=digest.attachments_list,
                    )
                    results.append(("regular", digest, publish_result))
                    try:
                        from monitoring.metrics import track_digest_published

                        track_digest_published(
                            region=region.code,
                            topic=theme,
                            result="success" if publish_result.success else "failed",
                        )
                    except (
                        Exception
                    ):  # pragma: no cover - metrics никогда не должны валить публикацию
                        logger.debug("track_digest_published failed", exc_info=True)

            # Mourning digest
            if mourning_posts:
                mourning_header, mourning_tags, mourning_local_hashtag = (
                    resolve_mourning_digest_format()
                )
                mourning_builder = DigestBuilder(
                    header=mourning_header,
                    hashtags=mourning_tags,
                    local_hashtag=mourning_local_hashtag,
                    max_text_length=region_config.text_post_maxsize_simbols or 4096,
                    max_posts_per_digest=pipeline_eff.get("max_posts_per_digest"),
                )
                mourning_digest = mourning_builder.build_digest(
                    mourning_posts, group_names=group_names
                )
                if mourning_digest.post_count == 0 or not mourning_digest.text.strip():
                    logger.warning(
                        "Empty mourning digest after build, skipping publish "
                        "(region=%s theme=%s candidates=%d)",
                        region.code,
                        theme,
                        len(mourning_posts),
                    )
                else:
                    selected_by_lip.update(
                        {
                            lip_of_post(
                                p.get("owner_id", p.get("from_id", 0)),
                                p.get("id", 0),
                            ): p
                            for p in mourning_posts
                        }
                    )

                    vk_pub = await VKPublisher.create_with_policy(
                        session,
                        target_group_id=region.vk_group_id,
                        test_polygon_mode=test_mode,
                    )
                    mourning_pub = await vk_pub.publish_digest(
                        group_id=region.vk_group_id,
                        text=mourning_digest.text,
                        attachments=mourning_digest.attachments_list,
                    )
                    results.append(("mourning", mourning_digest, mourning_pub))
                    try:
                        from monitoring.metrics import track_digest_published

                        track_digest_published(
                            region=region.code,
                            topic="mourning",
                            result="success" if mourning_pub.success else "failed",
                        )
                    except Exception:  # pragma: no cover
                        logger.debug("track_digest_published failed", exc_info=True)

            # 8. Update work table
            all_included = []
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
                for lip in all_included:
                    p = selected_by_lip.get(lip)
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
                                new_hash_entries.append(
                                    f"txtsim:{rafinad_len // 20}:{simhash}"
                                )

                    atts = p.get("attachments")
                    media_ids = create_media_fingerprint(
                        atts if isinstance(atts, list) else []
                    )
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

            # 9. Return result
            total_published = sum(d.post_count for _, d, _ in results)
            first_url = results[0][2].get("url") if results else None
            return {
                "success": (
                    all(r[2].get("success", False) for r in results)
                    if results
                    else True
                ),
                "posts_published": total_published,
                "published_url": first_url,
                "mourning_posts": len(mourning_posts),
                "regular_posts": len(regular_posts),
                "digests_count": len(results),
                "stats": parser_stats,
            }

    try:
        # Same persistent loop as other Celery tasks (see utils/celery_asyncio).
        result = run_coro(_execute())

        # Save stats (sync-friendly)
        try:

            async def _save_stats():
                async with AsyncSessionLocal() as session:
                    record = ParsingStats(
                        region_code=region_code,
                        theme=theme,
                        run_date=start_time,
                        run_type="scheduled",
                        duration_seconds=(datetime.now() - start_time).total_seconds(),
                        success=result.get("success", False),
                        total_groups_checked=result.get("stats", {}).get(
                            "total_groups_checked", 0
                        ),
                        total_posts_scanned=result.get("stats", {}).get(
                            "total_posts_scanned", 0
                        ),
                        posts_filtered_old=result.get("stats", {}).get(
                            "posts_filtered_old", 0
                        ),
                        posts_filtered_duplicate_lip=result.get("stats", {}).get(
                            "posts_filtered_duplicate_lip", 0
                        ),
                        posts_filtered_duplicate_text=result.get("stats", {}).get(
                            "posts_filtered_duplicate_text", 0
                        ),
                        posts_filtered_duplicate_foto=result.get("stats", {}).get(
                            "posts_filtered_duplicate_foto", 0
                        ),
                        posts_filtered_black_id=result.get("stats", {}).get(
                            "posts_filtered_black_id", 0
                        ),
                        posts_filtered_no_region_words=result.get("stats", {}).get(
                            "posts_filtered_no_region_words", 0
                        ),
                        posts_filtered_advertisement=result.get("stats", {}).get(
                            "posts_filtered_advertisement", 0
                        ),
                        posts_filtered_no_attachments=result.get("stats", {}).get(
                            "posts_filtered_no_attachments", 0
                        ),
                        posts_final_count=result.get("stats", {}).get(
                            "posts_final_count", 0
                        ),
                        published_to_test_polygon=test_mode,
                    )
                    session.add(record)
                    await session.commit()

            run_coro(_save_stats())
        except Exception as stats_err:
            logger.warning(f"Failed to save stats: {stats_err}")
        return result

    except Exception as e:
        logger.error(f"Task failed for {region_code}/{theme}: {e}")
        # Save failure stats
        try:

            async def _save_failure():
                async with AsyncSessionLocal() as session:
                    record = ParsingStats(
                        region_code=region_code,
                        theme=theme,
                        run_date=start_time,
                        run_type="scheduled",
                        duration_seconds=(datetime.now() - start_time).total_seconds(),
                        success=False,
                        error_message=str(e),  # noqa: F821 — closed over outer except clause
                    )
                    session.add(record)
                    await session.commit()

            run_coro(_save_failure())
        except Exception:
            pass
        raise self.retry(exc=e, countdown=60 * (2**self.request.retries))


@shared_task
def parse_reklama(region_code: str):
    return parse_and_publish_theme.delay(region_code, "reklama")


@shared_task
def parse_novost(region_code: str):
    return parse_and_publish_theme.delay(region_code, "novost")


@shared_task
def parse_kultura(region_code: str):
    return parse_and_publish_theme.delay(region_code, "kultura")


@shared_task
def parse_sport(region_code: str):
    return parse_and_publish_theme.delay(region_code, "sport")


@shared_task
def parse_sosed(region_code: str):
    return parse_and_publish_theme.delay(region_code, "sosed")


@shared_task
def run_all_regions_theme(theme: str):
    """Run parsing for specific theme across all regions."""
    from sqlalchemy import exists, select

    from database.connection import AsyncSessionLocal
    from database.models import Community, Region
    from database.models_extended import RegionConfig

    async def _get_regions():
        async with AsyncSessionLocal() as session:
            has_theme_communities = (
                select(Community.id)
                .where(
                    Community.region_id == Region.id,
                    Community.category == theme,
                    Community.is_active.is_(True),
                )
                .exists()
            )
            has_any_communities = (
                select(Community.id)
                .where(
                    Community.region_id == Region.id,
                    Community.is_active.is_(True),
                )
                .exists()
            )
            result = await session.execute(
                select(Region.code).where(
                    Region.is_active.is_(True),
                    Region.vk_group_id.isnot(None),
                    Region.code != "kirov_obl",
                    exists().where(RegionConfig.region_code == Region.code),
                    (has_theme_communities | has_any_communities),
                )
            )
            return list(result.scalars().all())

    regions = run_coro(_get_regions())
    results = []
    for rc in regions:
        r = parse_and_publish_theme.delay(rc, theme)
        results.append(r)
    return {"theme": theme, "regions": regions, "tasks": [r.id for r in results]}
