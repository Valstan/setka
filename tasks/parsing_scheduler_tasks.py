"""
Celery tasks for Postopus migration - replaces crontab scheduling

Migrated from old_postopus crontab entries to Celery Beat schedule.
Each theme/region combination gets its own scheduled task.
"""
from celery import shared_task
import logging
from typing import Dict, Any, List
from datetime import datetime
from types import SimpleNamespace

from utils.celery_asyncio import run_coro

logger = logging.getLogger(__name__)


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
    from database.models_extended import ParsingStats, RegionConfig, WorkTable
    from database.connection import AsyncSessionLocal
    from database.models import Community, Region
    from modules.vk_monitor.advanced_parser import AdvancedVKParser
    from modules.vk_monitor.vk_client import VKClient
    from modules.publisher.digest_builder import DigestBuilder
    from modules.publisher.digest_splitter import DigestSplitter
    from modules.publisher.vk_publisher_extended import VKPublisher
    from config.runtime import get_parse_tokens
    from sqlalchemy import select

    start_time = datetime.now()

    async def _execute():
        """Execute parsing and publishing pipeline."""
        async with AsyncSessionLocal() as session:
            # Псевдо-регион «copy» + тема «setka» — отдельный сетевой хаб (env COPY_SETKA_*), без RegionConfig.
            if region_code == "copy" and theme == "setka":
                from modules.copy_setka_network import execute_copy_setka_network

                return await execute_copy_setka_network(session, test_mode=test_mode)

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
                )

            # 2. Get work table
            result = await session.execute(
                select(WorkTable).where(
                    WorkTable.region_code == region_code,
                    WorkTable.theme == theme
                )
            )
            work_table = result.scalars().first()
            if not work_table:
                work_table = WorkTable(region_code=region_code, theme=theme, lip=[], hash=[])
                session.add(work_table)
                await session.commit()

            # 3. Get region
            region_result = await session.execute(
                select(Region).where(Region.code == region_code)
            )
            region = region_result.scalars().first()
            if not region or not region.vk_group_id:
                return {'success': False, 'error': f'No VK group ID for region {region_code}'}

            # 4. Get communities for this theme
            communities_result = await session.execute(
                select(Community.vk_id).where(
                    Community.region_id == region.id,
                    Community.category == theme,
                    Community.is_active == True
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
                        Community.region_id == region.id,
                        Community.is_active == True
                    )
                )
                community_ids = [row[0] for row in fallback_result.fetchall()]
                if not community_ids:
                    return {'success': False, 'error': 'No communities found'}

            # 5. Parse
            parse_tokens = get_parse_tokens()
            parse_token = next(iter(parse_tokens.values())) if parse_tokens else None
            if not parse_token:
                return {'success': False, 'error': 'No VK tokens configured'}
            vk_client = VKClient(parse_token)
            parser = AdvancedVKParser(vk_client)

            posts = await parser.parse_posts_from_communities(
                community_ids=community_ids,
                theme=theme,
                region_config=region_config,
                work_table_lip=work_table.lip or [],
                work_table_hash=work_table.hash or [],
                count_per_community=20,
            )
            parser_stats = parser.get_stats()

            # 6. Split by sentiment
            splitter = DigestSplitter()
            mourning_posts, regular_posts = splitter.split_posts(posts)
            logger.info(f"Split: {len(mourning_posts)} mourning, {len(regular_posts)} regular")

            # 7. Build digests
            header = (region_config.zagolovki or {}).get(theme, f"📰 {theme.title()}")
            heshteg = region_config.heshteg or {}
            hashtags = [heshteg[theme]] if theme in heshteg else []
            heshteg_local = region_config.heshteg_local or {}
            local_hashtag = f"#{heshteg_local.get('raicentr', '')}" if heshteg_local else ""

            results = []

            # Regular digest
            if regular_posts:
                builder = DigestBuilder(
                    header=header,
                    hashtags=hashtags,
                    local_hashtag=local_hashtag,
                    max_text_length=region_config.text_post_maxsize_simbols or 4096,
                    repost_mode=region_config.setka_regim_repost,
                )
                digest = builder.build_digest(regular_posts)

                vk_publisher = VKPublisher(test_polygon_mode=test_mode)
                publish_result = await vk_publisher.publish_digest(
                    group_id=region.vk_group_id,
                    text=digest.text,
                    attachments=digest.attachments_list,
                )
                results.append(('regular', digest, publish_result))

            # Mourning digest
            if mourning_posts:
                mourning_builder = DigestBuilder(
                    header='🕯 Скорбим',
                    hashtags=[],
                    local_hashtag='',
                    max_text_length=region_config.text_post_maxsize_simbols or 4096,
                )
                mourning_digest = mourning_builder.build_digest(mourning_posts)

                vk_pub = VKPublisher(test_polygon_mode=test_mode)
                mourning_pub = await vk_pub.publish_digest(
                    group_id=region.vk_group_id,
                    text=mourning_digest.text,
                    attachments=mourning_digest.attachments_list,
                )
                results.append(('mourning', mourning_digest, mourning_pub))

            # 8. Update work table
            all_included = []
            for _, d, _ in results:
                all_included.extend(d.posts_included)
            if all_included:
                existing = work_table.lip or []
                existing.extend(all_included)
                if len(existing) > 30:
                    existing = existing[-30:]
                work_table.lip = existing
                await session.commit()

            # 9. Return result
            total_published = sum(d.post_count for _, d, _ in results)
            first_url = results[0][2].get('url') if results else None
            return {
                'success': all(r[2].get('success', False) for r in results) if results else True,
                'posts_published': total_published,
                'published_url': first_url,
                'mourning_posts': len(mourning_posts),
                'regular_posts': len(regular_posts),
                'digests_count': len(results),
                'stats': parser_stats,
            }

    try:
        # Same persistent loop as other Celery tasks (see utils/celery_asyncio).
        result = run_coro(_execute())

        # Save stats (sync-friendly)
        try:
            async def _save_stats():
                async with AsyncSessionLocal() as session:
                    record = ParsingStats(
                        region_code=region_code, theme=theme,
                        run_date=start_time, run_type='scheduled',
                        duration_seconds=(datetime.now() - start_time).total_seconds(),
                        success=result.get('success', False),
                        total_groups_checked=result.get('stats', {}).get('total_groups_checked', 0),
                        total_posts_scanned=result.get('stats', {}).get('total_posts_scanned', 0),
                        posts_filtered_old=result.get('stats', {}).get('posts_filtered_old', 0),
                        posts_filtered_duplicate_lip=result.get('stats', {}).get('posts_filtered_duplicate_lip', 0),
                        posts_filtered_duplicate_text=result.get('stats', {}).get('posts_filtered_duplicate_text', 0),
                        posts_filtered_duplicate_foto=result.get('stats', {}).get('posts_filtered_duplicate_foto', 0),
                        posts_filtered_black_id=result.get('stats', {}).get('posts_filtered_black_id', 0),
                        posts_filtered_no_region_words=result.get('stats', {}).get('posts_filtered_no_region_words', 0),
                        posts_filtered_advertisement=result.get('stats', {}).get('posts_filtered_advertisement', 0),
                        posts_filtered_no_attachments=result.get('stats', {}).get('posts_filtered_no_attachments', 0),
                        posts_final_count=result.get('stats', {}).get('posts_final_count', 0),
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
                        region_code=region_code, theme=theme,
                        run_date=start_time, run_type='scheduled',
                        duration_seconds=(datetime.now() - start_time).total_seconds(),
                        success=False, error_message=str(e),
                    )
                    session.add(record)
                    await session.commit()
            run_coro(_save_failure())
        except Exception:
            pass
        raise self.retry(exc=e, countdown=60 * (2 ** self.request.retries))


@shared_task
def parse_reklama(region_code: str):
    return parse_and_publish_theme.delay(region_code, 'reklama')

@shared_task
def parse_novost(region_code: str):
    return parse_and_publish_theme.delay(region_code, 'novost')

@shared_task
def parse_kultura(region_code: str):
    return parse_and_publish_theme.delay(region_code, 'kultura')

@shared_task
def parse_sport(region_code: str):
    return parse_and_publish_theme.delay(region_code, 'sport')

@shared_task
def parse_sosed(region_code: str):
    return parse_and_publish_theme.delay(region_code, 'sosed')


@shared_task
def run_all_regions_theme(theme: str):
    """Run parsing for specific theme across all regions."""
    from database.models import Region, Community
    from database.models_extended import RegionConfig
    from database.connection import AsyncSessionLocal
    from sqlalchemy import select, exists

    async def _get_regions():
        async with AsyncSessionLocal() as session:
            has_theme_communities = (
                select(Community.id)
                .where(
                    Community.region_id == Region.id,
                    Community.category == theme,
                    Community.is_active == True,
                )
                .exists()
            )
            has_any_communities = (
                select(Community.id)
                .where(
                    Community.region_id == Region.id,
                    Community.is_active == True,
                )
                .exists()
            )
            result = await session.execute(
                select(Region.code).where(
                    Region.is_active == True,
                    Region.vk_group_id.isnot(None),
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
    return {'theme': theme, 'regions': regions, 'tasks': [r.id for r in results]}
