"""
Publishing tasks - content distribution to platforms
"""
import asyncio
import logging
from celery import Task
from celery_app import app
from datetime import datetime
from typing import List

from modules.publisher.publisher import ContentPublisher
from config.config_secure import VK_TOKENS, TELEGRAM_TOKENS
from database.connection import AsyncSessionLocal
from database.models import Region
from sqlalchemy import select

logger = logging.getLogger(__name__)


class AsyncTask(Task):
    """Base task class for async operations"""
    
    def __call__(self, *args, **kwargs):
        """Run async function in event loop"""
        loop = asyncio.get_event_loop()
        return loop.run_until_complete(self.run_async(*args, **kwargs))
    
    async def run_async(self, *args, **kwargs):
        """Override this method in child classes"""
        raise NotImplementedError


def get_publisher() -> ContentPublisher:
    """Initialize and return ContentPublisher"""
    # Get first available VK token for posting
    vk_token = None
    for key, token in VK_TOKENS.items():
        if token and key in ["VALSTAN", "OLGA", "VITA"]:
            vk_token = token
            break
    
    # Get Telegram token
    telegram_token = TELEGRAM_TOKENS.get("AFONYA")
    
    return ContentPublisher(
        vk_token=vk_token,
        telegram_token=telegram_token,
        wordpress_config=None  # TODO: Add WordPress config when needed
    )


@app.task(base=AsyncTask, bind=True, name='tasks.publishing_tasks.publish_scheduled_posts')
async def publish_scheduled_posts(self):
    """
    Publish scheduled posts for all regions
    
    Runs hourly
    """
    logger.info("📤 Publishing scheduled posts...")
    
    try:
        publisher = get_publisher()
        
        # Get all active regions
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Region).where(Region.is_active == True)
            )
            regions = result.scalars().all()
        
        total_published = 0
        results = {}
        
        for region in regions:
            try:
                # Publish to VK and Telegram
                platforms = ['vk', 'telegram']
                
                result = await publisher.publish_approved_posts(
                    region_code=region.code,
                    platforms=platforms,
                    limit=5  # Max 5 posts per region per hour
                )
                
                results[region.code] = result
                total_published += result.get('published', 0)
                
                logger.info(
                    f"Region {region.code}: {result.get('published', 0)}/{result.get('total', 0)} published"
                )
                
                # Delay between regions
                await asyncio.sleep(2)
                
            except Exception as e:
                logger.error(f"Failed to publish for region {region.code}: {e}")
                results[region.code] = {'error': str(e)}
        
        logger.info(f"✅ Publishing completed: {total_published} posts published")
        
        return {
            'status': 'success',
            'timestamp': datetime.utcnow().isoformat(),
            'total_published': total_published,
            'regions': results
        }
        
    except Exception as e:
        logger.error(f"❌ Publishing failed: {e}")
        return {
            'status': 'failed',
            'error': str(e)
        }


@app.task(base=AsyncTask, bind=True, name='tasks.publishing_tasks.publish_post')
async def publish_post(
    self,
    post_id: int,
    platforms: List[str],
    region_code: str
):
    """
    Publish specific post
    
    Args:
        post_id: Post ID to publish
        platforms: List of platforms ('vk', 'telegram', 'wordpress')
        region_code: Region code
    """
    logger.info(f"📤 Publishing post {post_id} to {platforms}...")
    
    try:
        publisher = get_publisher()
        
        result = await publisher.publish_post(
            post_id=post_id,
            platforms=platforms,
            region_code=region_code
        )
        
        if result.get('error'):
            logger.error(f"❌ Publishing failed: {result['error']}")
            return result
        
        success_count = sum(
            1 for r in result['results'].values()
            if r.get('success')
        )
        
        logger.info(f"✅ Post {post_id} published to {success_count}/{len(platforms)} platforms")
        
        return result
        
    except Exception as e:
        logger.error(f"❌ Publishing failed: {e}")
        return {
            'status': 'failed',
            'error': str(e)
        }


@app.task(base=AsyncTask, bind=True, name='tasks.publishing_tasks.publish_region')
async def publish_region(
    self,
    region_code: str,
    platforms: List[str],
    limit: int = 10
):
    """
    Publish approved posts for specific region
    
    Args:
        region_code: Region code
        platforms: List of platforms
        limit: Maximum posts to publish
    """
    logger.info(f"📤 Publishing region {region_code}...")
    
    try:
        publisher = get_publisher()
        
        result = await publisher.publish_approved_posts(
            region_code=region_code,
            platforms=platforms,
            limit=limit
        )
        
        logger.info(
            f"✅ Region {region_code}: {result.get('published', 0)}/{result.get('total', 0)} published"
        )
        
        return result
        
    except Exception as e:
        logger.error(f"❌ Publishing region failed: {e}")
        return {
            'status': 'failed',
            'error': str(e)
        }


@app.task(base=AsyncTask, bind=True, name='tasks.publishing_tasks.check_publishers')
async def check_publishers(self):
    """
    Check all publisher connections
    
    Returns connection status for all platforms
    """
    logger.info("🔍 Checking publisher connections...")
    
    try:
        publisher = get_publisher()
        results = await publisher.check_all_connections()
        
        return {
            'status': 'success',
            'timestamp': datetime.utcnow().isoformat(),
            'platforms': results
        }
        
    except Exception as e:
        logger.error(f"❌ Connection check failed: {e}")
        return {
            'status': 'failed',
            'error': str(e)
        }

