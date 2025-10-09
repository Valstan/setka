"""
Monitoring tasks - VK scanning and health checks
"""
import asyncio
import logging
from celery import Task
from celery_app import app
from datetime import datetime, timedelta

from modules.vk_monitor.monitor import VKMonitor
from modules.monitoring.health import HealthCheck
from config.config_secure import VK_TOKENS
from database.connection import AsyncSessionLocal
from database.models import Post
from sqlalchemy import select, and_

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


@app.task(base=AsyncTask, bind=True, name='tasks.monitoring_tasks.scan_all_communities')
async def scan_all_communities(self):
    """
    Scan all VK communities for new posts
    
    Runs every 5 minutes
    """
    logger.info("🔍 Starting VK communities scan...")
    
    try:
        # Get VK tokens
        tokens = [token for token in VK_TOKENS.values() if token]
        
        if not tokens:
            logger.error("No VK tokens available")
            return {'error': 'No tokens'}
        
        # Initialize monitor
        monitor = VKMonitor(tokens)
        
        # Scan all regions
        results = await monitor.scan_all_regions()
        
        logger.info(f"✅ Scan completed: {results['total_new_posts']} new posts found")
        
        return {
            'status': 'success',
            'timestamp': datetime.utcnow().isoformat(),
            'regions': results['regions_scanned'],
            'communities': results['total_communities'],
            'new_posts': results['total_new_posts']
        }
        
    except Exception as e:
        logger.error(f"❌ Scan failed: {e}")
        return {
            'status': 'failed',
            'error': str(e)
        }


@app.task(base=AsyncTask, bind=True, name='tasks.monitoring_tasks.scan_region')
async def scan_region(self, region_code: str):
    """
    Scan specific region
    
    Args:
        region_code: Region code to scan
    """
    logger.info(f"🔍 Scanning region: {region_code}")
    
    try:
        tokens = [token for token in VK_TOKENS.values() if token]
        monitor = VKMonitor(tokens)
        
        result = await monitor.scan_region(region_code)
        
        logger.info(f"✅ Region {region_code} scanned: {result.get('new_posts', 0)} new posts")
        
        return result
        
    except Exception as e:
        logger.error(f"❌ Region scan failed: {e}")
        return {
            'status': 'failed',
            'region': region_code,
            'error': str(e)
        }


@app.task(base=AsyncTask, bind=True, name='tasks.monitoring_tasks.health_check')
async def health_check(self):
    """
    System health check
    
    Runs every minute
    """
    try:
        health = HealthCheck()
        status = await health.check_all()
        
        if not status['healthy']:
            logger.warning(f"⚠️ System health issues detected")
            # TODO: Send alert to Telegram
        
        return status
        
    except Exception as e:
        logger.error(f"❌ Health check failed: {e}")
        return {
            'healthy': False,
            'error': str(e)
        }


@app.task(base=AsyncTask, bind=True, name='tasks.monitoring_tasks.cleanup_old_data')
async def cleanup_old_data(self):
    """
    Cleanup old rejected posts and data
    
    Runs daily at 3:30 AM
    """
    logger.info("🧹 Starting data cleanup...")
    
    try:
        async with AsyncSessionLocal() as session:
            # Delete rejected posts older than 30 days
            cutoff_date = datetime.utcnow() - timedelta(days=30)
            
            result = await session.execute(
                select(Post).where(
                    and_(
                        Post.status == 'rejected',
                        Post.created_at < cutoff_date
                    )
                )
            )
            old_posts = result.scalars().all()
            
            deleted_count = 0
            for post in old_posts:
                await session.delete(post)
                deleted_count += 1
            
            await session.commit()
            
            logger.info(f"✅ Cleanup completed: {deleted_count} old posts deleted")
            
            return {
                'status': 'success',
                'deleted_posts': deleted_count
            }
            
    except Exception as e:
        logger.error(f"❌ Cleanup failed: {e}")
        return {
            'status': 'failed',
            'error': str(e)
        }

