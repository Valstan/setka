"""
Celery tasks for Postopus migration - replaces crontab scheduling

Migrated from old_postopus crontab entries to Celery Beat schedule.
Each theme/region combination gets its own scheduled task.
"""
from celery import shared_task
import logging
from typing import Dict, Any, List
from datetime import datetime

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
    Celery-compatible: uses new event loop per invocation.
    """
    import asyncio
    from database.models_extended import ParsingStats, RegionConfig, WorkTable
    from database.connection import AsyncSessionLocal
    from database.models import Community, Region
    from modules.vk_monitor.advanced_parser import AdvancedVKParser
    from modules.vk_monitor.vk_client import VKClient
    from modules.publisher.digest_builder import DigestBuilder
    from modules.publisher.digest_splitter import DigestSplitter
    from modules.publisher.vk_publisher_extended import VKPublisher
    from config.runtime import get_parse_tokens
    from utils.post_utils import lip_of_post
    from sqlalchemy import select

    start_time = datetime.now()

    async def _execute():
        """Execute parsing and publishing pipeline."""
        async with AsyncSessionLocal() as session:
            # 1. Get region config
            result = await session.execute(
                select(RegionConfig).where(RegionConfig.region_code == region_code)
            )
            region_config = result.scalars().first()
            if not region_config:
                return {'success': False, 'error': f'RegionConfig not found for {region_code}'}

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
                logger.warning(f"No communities found for {region_code}/{theme}")
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

    from concurrent.futures import ThreadPoolExecutor
    import concurrent.futures

    def _run_async(coro):
        """Run async coroutine in a SEPARATE THREAD with its own event loop."""
        result = [None]
        error = [None]
        
        def _thread_target():
            loop = asyncio.new_event_loop()
            try:
                asyncio.set_event_loop(loop)
                result[0] = loop.run_until_complete(coro)
            except Exception as e:
                error[0] = e
            finally:
                loop.close()
        
        t = __import__('threading').Thread(target=_thread_target)
        t.start()
        t.join(timeout=300)
        if error[0]:
            raise error[0]
        if t.is_alive():
            raise TimeoutError("Async task timed out after 300s")
        return result[0]

    try:
        result = _run_async(_execute())

        return result

    except Exception as e:
        logger.error(f"Task failed for {region_code}/{theme}: {e}")
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
    from database.models import Region
    from database.connection import AsyncSessionLocal
    from sqlalchemy import select
    import asyncio

    def _run_async(coro):
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    async def _get_regions():
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Region).where(Region.is_active == True)
            )
            return [row.code for row in result.scalars().all()]

    regions = _run_async(_get_regions())
    results = []
    for rc in regions:
        r = parse_and_publish_theme.delay(rc, theme)
        results.append(r)
    return {'theme': theme, 'regions': regions, 'tasks': [r.id for r in results]}
