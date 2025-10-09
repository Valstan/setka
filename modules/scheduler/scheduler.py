"""
Content Scheduler - orchestrates automated workflows
"""
import asyncio
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from database.connection import AsyncSessionLocal
from database.models import Post, Region, PublishSchedule
from modules.vk_monitor.monitor import VKMonitor
from modules.ai_analyzer.analyzer import PostAnalyzer
from modules.publisher.publisher import ContentPublisher

logger = logging.getLogger(__name__)


class ContentScheduler:
    """
    Main scheduler that orchestrates the full content pipeline:
    VK Monitor → AI Analyzer → Publisher
    """
    
    def __init__(
        self,
        vk_tokens: List[str],
        groq_api_key: Optional[str],
        publisher: ContentPublisher
    ):
        """
        Initialize Content Scheduler
        
        Args:
            vk_tokens: List of VK API tokens
            groq_api_key: Groq API key for AI analysis
            publisher: ContentPublisher instance
        """
        self.vk_monitor = VKMonitor(vk_tokens)
        self.ai_analyzer = PostAnalyzer(groq_api_key)
        self.publisher = publisher
        self.running = False
    
    async def run_full_cycle(self, region_code: Optional[str] = None) -> Dict[str, Any]:
        """
        Run full content processing cycle:
        1. Monitor VK → 2. Analyze posts → 3. Publish approved
        
        Args:
            region_code: Process specific region, or all if None
            
        Returns:
            Cycle statistics
        """
        logger.info("🔄 Starting full content cycle...")
        start_time = datetime.utcnow()
        
        try:
            # Step 1: Monitor VK for new posts
            logger.info("📥 Step 1: Monitoring VK communities...")
            if region_code:
                monitor_result = await self.vk_monitor.scan_region(region_code)
                new_posts = monitor_result.get('new_posts', 0)
            else:
                monitor_result = await self.vk_monitor.scan_all_regions()
                new_posts = monitor_result.get('total_new_posts', 0)
            
            logger.info(f"✅ Found {new_posts} new posts")
            
            # Small delay
            await asyncio.sleep(2)
            
            # Step 2: Analyze new posts with AI
            logger.info("🤖 Step 2: Analyzing posts with AI...")
            analysis_result = await self.ai_analyzer.analyze_new_posts(limit=100)
            analyzed = analysis_result.get('analyzed', 0)
            approved = analysis_result.get('approved', 0)
            
            logger.info(f"✅ Analyzed {analyzed} posts, {approved} approved")
            
            # Small delay
            await asyncio.sleep(2)
            
            # Step 3: Publish approved posts
            logger.info("📤 Step 3: Publishing approved posts...")
            publish_stats = await self._publish_by_schedule(region_code)
            published = publish_stats.get('total_published', 0)
            
            logger.info(f"✅ Published {published} posts")
            
            # Calculate duration
            duration = (datetime.utcnow() - start_time).total_seconds()
            
            result = {
                'status': 'success',
                'timestamp': start_time.isoformat(),
                'duration_seconds': duration,
                'monitoring': {
                    'new_posts': new_posts
                },
                'analysis': {
                    'analyzed': analyzed,
                    'approved': approved,
                    'rejected': analysis_result.get('rejected', 0)
                },
                'publishing': {
                    'published': published
                }
            }
            
            logger.info(f"✅ Full cycle completed in {duration:.1f}s")
            return result
            
        except Exception as e:
            logger.error(f"❌ Full cycle failed: {e}")
            return {
                'status': 'failed',
                'error': str(e)
            }
    
    async def _publish_by_schedule(
        self,
        region_code: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Publish posts according to region schedules
        
        Args:
            region_code: Specific region or all
            
        Returns:
            Publishing statistics
        """
        async with AsyncSessionLocal() as session:
            # Get regions
            if region_code:
                result = await session.execute(
                    select(Region).where(
                        and_(
                            Region.code == region_code,
                            Region.is_active == True
                        )
                    )
                )
                regions = [result.scalar_one_or_none()]
            else:
                result = await session.execute(
                    select(Region).where(Region.is_active == True)
                )
                regions = result.scalars().all()
            
            total_published = 0
            results = {}
            
            for region in regions:
                if not region:
                    continue
                
                try:
                    # Determine platforms based on region config
                    platforms = []
                    if region.vk_group_id:
                        platforms.append('vk')
                    if region.telegram_channel:
                        platforms.append('telegram')
                    
                    if not platforms:
                        logger.warning(f"No platforms configured for region {region.code}")
                        continue
                    
                    # Publish approved posts
                    result = await self.publisher.publish_approved_posts(
                        region_code=region.code,
                        platforms=platforms,
                        limit=5  # Max 5 posts per cycle
                    )
                    
                    results[region.code] = result
                    total_published += result.get('published', 0)
                    
                    # Delay between regions
                    await asyncio.sleep(2)
                    
                except Exception as e:
                    logger.error(f"Failed to publish for region {region.code}: {e}")
                    results[region.code] = {'error': str(e)}
            
            return {
                'total_published': total_published,
                'regions': results
            }
    
    async def get_schedule_status(self) -> Dict[str, Any]:
        """
        Get current schedule status for all regions
        
        Returns:
            Schedule information
        """
        async with AsyncSessionLocal() as session:
            # Get all schedules
            result = await session.execute(
                select(PublishSchedule).where(PublishSchedule.is_active == True)
            )
            schedules = result.scalars().all()
            
            schedule_info = []
            for schedule in schedules:
                # Get region info
                result = await session.execute(
                    select(Region).where(Region.id == schedule.region_id)
                )
                region = result.scalar_one_or_none()
                
                if region:
                    schedule_info.append({
                        'region': region.code,
                        'category': schedule.category,
                        'time': f"{schedule.hour:02d}:{schedule.minute:02d}",
                        'days': schedule.days_of_week,
                        'last_run': schedule.last_run.isoformat() if schedule.last_run else None
                    })
            
            return {
                'total_schedules': len(schedule_info),
                'schedules': schedule_info
            }
    
    async def get_pipeline_stats(self) -> Dict[str, Any]:
        """
        Get statistics for the entire content pipeline
        
        Returns:
            Pipeline statistics
        """
        async with AsyncSessionLocal() as session:
            # Count posts by status
            result = await session.execute(select(Post))
            all_posts = result.scalars().all()
            
            stats = {
                'total_posts': len(all_posts),
                'by_status': {},
                'by_platform': {
                    'vk': sum(1 for p in all_posts if p.published_vk),
                    'telegram': sum(1 for p in all_posts if p.published_telegram),
                    'wordpress': sum(1 for p in all_posts if p.published_wordpress)
                }
            }
            
            # Count by status
            for post in all_posts:
                status = post.status
                stats['by_status'][status] = stats['by_status'].get(status, 0) + 1
            
            # Calculate pending posts
            pending = stats['by_status'].get('new', 0)
            to_analyze = stats['by_status'].get('analyzed', 0)
            ready_to_publish = stats['by_status'].get('approved', 0)
            
            stats['pipeline'] = {
                'pending_scan': 0,  # Can't determine without more data
                'pending_analysis': pending,
                'pending_review': to_analyze,
                'ready_to_publish': ready_to_publish,
                'published': stats['by_status'].get('published', 0),
                'rejected': stats['by_status'].get('rejected', 0)
            }
            
            return stats
    
    async def start_continuous_mode(
        self,
        cycle_interval: int = 300,
        region_code: Optional[str] = None
    ):
        """
        Start continuous automated mode
        
        Args:
            cycle_interval: Seconds between cycles (default: 5 minutes)
            region_code: Process specific region or all
        """
        self.running = True
        logger.info(f"🚀 Starting continuous mode (interval: {cycle_interval}s)")
        
        while self.running:
            try:
                logger.info("=" * 60)
                logger.info("=== Starting automated cycle ===")
                logger.info("=" * 60)
                
                result = await self.run_full_cycle(region_code)
                
                if result.get('status') == 'success':
                    logger.info(f"✅ Cycle completed successfully")
                else:
                    logger.error(f"❌ Cycle failed: {result.get('error')}")
                
                # Wait for next cycle
                logger.info(f"⏳ Waiting {cycle_interval}s until next cycle...")
                await asyncio.sleep(cycle_interval)
                
            except Exception as e:
                logger.error(f"❌ Error in continuous mode: {e}")
                await asyncio.sleep(60)  # Wait 1 minute on error
    
    def stop_continuous_mode(self):
        """Stop continuous mode"""
        self.running = False
        logger.info("🛑 Stopping continuous mode")

