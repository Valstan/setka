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
    
    Replaces crontab entries like:
    40 6,11,12,16,18,20 * * *  start_paket.py novost
    
    Args:
        region_code: Region code (mi, vp, ur, etc.)
        theme: Theme (novost, kultura, sport, etc.)
        test_mode: If True, post to test polygon
    
    Returns:
        Execution result dict
    """
    from database.models_extended import ParsingStats, RegionConfig, WorkTable
    from database.connection import async_session_maker
    from modules.vk_monitor.advanced_parser import AdvancedVKParser
    from modules.publisher.digest_builder import DigestBuilder
    from modules.publisher.vk_publisher_extended import VKPublisher
    from utils.post_utils import lip_of_post
    
    import asyncio
    
    start_time = datetime.now()
    stats_data = None
    
    try:
        async def _execute():
            """Execute parsing and publishing pipeline."""
            async with async_session_maker() as session:
                # Get region config
                result = await session.execute(
                    RegionConfig.__table__.select().where(
                        RegionConfig.region_code == region_code
                    )
                )
                region_config = result.scalar_one_or_none()
                
                if not region_config:
                    logger.error(f"Region config not found for {region_code}")
                    return {'success': False, 'error': 'Region config not found'}
                
                # Get work table
                result = await session.execute(
                    WorkTable.__table__.select().where(
                        WorkTable.region_code == region_code,
                        WorkTable.theme == theme
                    )
                )
                work_table = result.scalar_one_or_none()
                
                # Initialize parser
                from modules.vk_monitor.vk_client import VKAPIClient
                vk_client = VKAPIClient()
                parser = AdvancedVKParser(vk_client)
                
                # Get community IDs for this theme
                # This would query the communities table
                communities_result = await session.execute(
                    # Simplified - would need actual community fetching
                    f"SELECT vk_id FROM communities WHERE region_id IN "
                    f"(SELECT id FROM regions WHERE code='{region_code}') "
                    f"AND category='{theme}' AND is_active=true"
                )
                # This is pseudocode - actual implementation depends on DB structure
                community_ids = [row[0] for row in communities_result.fetchall()]
                
                if not community_ids:
                    logger.warning(f"No communities found for {region_code}/{theme}")
                    return {'success': False, 'error': 'No communities found'}
                
                # Parse posts
                posts = await parser.parse_posts_from_communities(
                    community_ids=community_ids,
                    theme=theme,
                    region_config=region_config,
                    work_table_lip=work_table.lip if work_table else [],
                    work_table_hash=work_table.hash if work_table else [],
                )
                
                parser_stats = parser.get_stats()
                
                # Build digest
                header = (region_config.zagolovki or {}).get(theme, f"📰 {theme.title()}")
                hashtags = []
                heshteg = region_config.heshteg or {}
                if theme in heshteg:
                    hashtags.append(heshteg[theme])
                
                local_hashtag = ""
                heshteg_local = region_config.heshteg_local or {}
                local_hashtag = f"#{heshteg_local.get('raicentr', '')}" if heshteg_local else ""
                
                builder = DigestBuilder(
                    header=header,
                    hashtags=hashtags,
                    local_hashtag=local_hashtag,
                    max_text_length=region_config.text_post_maxsize_simbols or 4096,
                    repost_mode=region_config.setka_regim_repost,
                )
                
                if not posts:
                    logger.info(f"No posts to publish for {region_code}/{theme}")
                    return {
                        'success': True,
                        'posts_published': 0,
                        'stats': parser_stats,
                    }
                
                digest_result = builder.build_digest(posts)
                
                # Publish
                vk_publisher = VKPublisher(
                    vk_client,
                    test_polygon_mode=test_mode,
                )
                
                # Get target group ID
                from database.models import Region
                region_result = await session.execute(
                    Region.__table__.select().where(Region.code == region_code)
                )
                region = region_result.scalar_one_or_none()
                
                if not region or not region.vk_group_id:
                    return {'success': False, 'error': 'No VK group ID for region'}
                
                publish_result = await vk_publisher.publish_digest(
                    group_id=region.vk_group_id,
                    text=digest_result.text,
                    attachments=digest_result.attachments_list,
                )
                
                # Update work table lip
                if publish_result.get('success') and work_table:
                    existing_lip = work_table.lip or []
                    existing_lip.extend(digest_result.posts_included)
                    
                    # Trim to reasonable size (keep last 30)
                    if len(existing_lip) > 30:
                        existing_lip = existing_lip[-30:]
                    
                    work_table.lip = existing_lip
                    await session.commit()
                
                # Prepare stats
                return {
                    'success': publish_result.get('success', False),
                    'posts_published': digest_result.post_count,
                    'published_url': publish_result.get('url'),
                    'stats': parser_stats,
                }
        
        # Run async code
        result = asyncio.run(_execute())
        
        # Record stats
        async def _save_stats():
            async with async_session_maker() as session:
                stats_record = ParsingStats(
                    region_code=region_code,
                    theme=theme,
                    run_date=start_time,
                    run_type='scheduled',
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
                session.add(stats_record)
                await session.commit()
        
        asyncio.run(_save_stats())
        
        return result
        
    except Exception as e:
        logger.error(f"Task failed for {region_code}/{theme}: {e}")
        
        # Save failure stats
        try:
            async def _save_failure():
                async with async_session_maker() as session:
                    stats_record = ParsingStats(
                        region_code=region_code,
                        theme=theme,
                        run_date=start_time,
                        run_type='scheduled',
                        duration_seconds=(datetime.now() - start_time).total_seconds(),
                        success=False,
                        error_message=str(e),
                    )
                    session.add(stats_record)
                    await session.commit()
            
            asyncio.run(_save_failure())
        except:
            pass
        
        # Retry on failure
        raise self.retry(exc=e, countdown=60 * (2 ** self.request.retries))


@shared_task
def parse_reklama(region_code: str):
    """Parse and publish reklama (ads)."""
    return parse_and_publish_theme.delay(region_code, 'reklama')


@shared_task
def parse_novost(region_code: str):
    """Parse and publish novost (news)."""
    return parse_and_publish_theme.delay(region_code, 'novost')


@shared_task
def parse_kultura(region_code: str):
    """Parse and publish kultura (culture)."""
    return parse_and_publish_theme.delay(region_code, 'kultura')


@shared_task
def parse_sport(region_code: str):
    """Parse and publish sport (sports)."""
    return parse_and_publish_theme.delay(region_code, 'sport')


@shared_task
def parse_sosed(region_code: str):
    """Parse and publish sosed (neighbor news)."""
    return parse_and_publish_theme.delay(region_code, 'sosed')


@shared_task
def run_all_regions_theme(theme: str):
    """Run parsing for specific theme across all regions."""
    from database.models import Region
    from database.connection import async_session_maker
    import asyncio
    
    async def _get_regions():
        async with async_session_maker() as session:
            result = await session.execute(
                Region.__table__.select().where(Region.is_active == True)
            )
            return [row.code for row in result.fetchall()]
    
    regions = asyncio.run(_get_regions())
    
    results = []
    for region_code in regions:
        result = parse_and_publish_theme.delay(region_code, theme)
        results.append(result)
    
    return {'theme': theme, 'regions': regions, 'tasks': [r.id for r in results]}
