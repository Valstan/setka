"""
Monitoring tasks - VK scanning and health checks
"""

import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy import and_, select

from celery_app import app
from database.connection import AsyncSessionLocal
from database.models import Post
from modules.monitoring.health_checker import HealthChecker
from modules.vk_monitor.monitor import VKMonitor

logger = logging.getLogger(__name__)


async def _active_read_tokens() -> list:
    """Живые READ-токены из БД (единый источник 2026-07-12, был env)."""
    from modules.vk_token_router import get_active_parse_tokens

    async with AsyncSessionLocal() as session:
        return [t for t in (await get_active_parse_tokens(session)).values() if t]


@app.task(bind=True, name="tasks.monitoring_tasks.scan_all_communities")
def scan_all_communities(self):
    """
    Scan all VK communities for new posts

    Runs every 5 minutes
    """
    logger.info("🔍 Starting VK communities scan...")

    try:
        # Запускаем async функцию в event loop
        result = asyncio.run(_scan_all_communities_async())

        logger.info(f"✅ VK scan complete: {result.get('total_posts', 0)} posts found")

        return result

    except Exception as e:
        logger.error(f"❌ VK scan failed: {e}")
        raise


async def _scan_all_communities_async():

    try:
        # Get VK tokens (БД — единый источник)
        tokens = await _active_read_tokens()

        if not tokens:
            logger.error("No VK tokens available")
            return {"error": "No tokens"}

        # Initialize monitor
        monitor = VKMonitor(tokens)

        # Scan all regions
        results = await monitor.scan_all_regions()

        logger.info(f"✅ Scan completed: {results['total_new_posts']} new posts found")

        return {
            "status": "success",
            "timestamp": datetime.utcnow().isoformat(),
            "regions": results["regions_scanned"],
            "communities": results["total_communities"],
            "new_posts": results["total_new_posts"],
        }

    except Exception as e:
        logger.error(f"❌ Scan failed: {e}")
        return {"status": "failed", "error": str(e)}


@app.task(bind=True, name="tasks.monitoring_tasks.scan_region")
def scan_region(self, region_code: str):
    """
    Scan specific region

    Args:
        region_code: Region code to scan
    """
    logger.info(f"🔍 Scanning region: {region_code}")

    try:
        # Запускаем async функцию в event loop
        result = asyncio.run(_scan_region_async(region_code))

        logger.info(f"✅ Region {region_code} scanned: {result.get('new_posts', 0)} new posts")

        return result

    except Exception as e:
        logger.error(f"❌ Region scan failed: {e}")
        raise


async def _scan_region_async(region_code: str):

    try:
        tokens = await _active_read_tokens()
        monitor = VKMonitor(tokens)

        result = await monitor.scan_region(region_code)

        logger.info(f"✅ Region {region_code} scanned: {result.get('new_posts', 0)} new posts")

        return result

    except Exception as e:
        logger.error(f"❌ Region scan failed: {e}")
        return {"status": "failed", "region": region_code, "error": str(e)}


@app.task(bind=True, name="tasks.monitoring_tasks.health_check")
def health_check(self):
    """
    System health check

    Runs every minute
    """
    logger.info("🏥 Starting health check...")

    try:
        # Запускаем async функцию в event loop
        result = asyncio.run(_health_check_async())

        logger.info(f"✅ Health check complete: {result.get('status', 'unknown')}")

        return result

    except Exception as e:
        logger.error(f"❌ Health check failed: {e}")
        raise


async def _health_check_async():
    try:
        health = HealthChecker()
        status = await health.full_health_check()

        if status["status"] != "healthy":
            logger.warning("⚠️ System health issues detected")
            # TODO: Send alert to Telegram

        return status

    except Exception as e:
        logger.error(f"❌ Health check failed: {e}")
        return {"healthy": False, "error": str(e)}


@app.task(bind=True, name="tasks.monitoring_tasks.cleanup_old_data")
def cleanup_old_data(self):
    """
    Cleanup old rejected posts and data

    Runs daily at 3:30 AM
    """
    logger.info("🧹 Starting data cleanup...")

    try:
        # Запускаем async функцию в event loop
        result = asyncio.run(_cleanup_old_data_async())

        logger.info(f"✅ Cleanup complete: {result.get('deleted_posts', 0)} posts deleted")

        return result

    except Exception as e:
        logger.error(f"❌ Cleanup failed: {e}")
        raise


async def _cleanup_old_data_async():
    try:
        async with AsyncSessionLocal() as session:
            # Delete rejected posts older than 30 days
            cutoff_date = datetime.utcnow() - timedelta(days=30)

            result = await session.execute(
                select(Post).where(and_(Post.status == "rejected", Post.created_at < cutoff_date))
            )
            old_posts = result.scalars().all()

            deleted_count = 0
            for post in old_posts:
                await session.delete(post)
                deleted_count += 1

            await session.commit()

            logger.info(f"✅ Cleanup completed: {deleted_count} old posts deleted")

            return {"status": "success", "deleted_posts": deleted_count}

    except Exception as e:
        logger.error(f"❌ Cleanup failed: {e}")
        return {"status": "failed", "error": str(e)}
