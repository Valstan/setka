"""
Main Content Publisher - orchestrates publishing to multiple platforms
"""
import asyncio
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_

from database.connection import AsyncSessionLocal
from database.models import Post, Region
from modules.publisher.vk_publisher import VKPublisher
from modules.publisher.telegram_publisher import TelegramPublisher
from modules.publisher.wordpress_publisher import WordPressPublisher

logger = logging.getLogger(__name__)


class ContentPublisher:
    """Main publisher that coordinates all platforms"""
    
    def __init__(
        self,
        vk_token: Optional[str] = None,
        telegram_token: Optional[str] = None,
        wordpress_config: Optional[Dict[str, str]] = None
    ):
        """
        Initialize Content Publisher
        
        Args:
            vk_token: VK API token for posting
            telegram_token: Telegram Bot token
            wordpress_config: WordPress configuration dict with:
                - site_url
                - username
                - app_password
        """
        self.publishers = {}
        
        # Initialize VK publisher
        if vk_token:
            try:
                self.publishers['vk'] = VKPublisher(vk_token)
                logger.info("VK Publisher initialized")
            except Exception as e:
                logger.error(f"Failed to initialize VK Publisher: {e}")
        
        # Initialize Telegram publisher
        if telegram_token:
            try:
                self.publishers['telegram'] = TelegramPublisher(telegram_token)
                logger.info("Telegram Publisher initialized")
            except Exception as e:
                logger.error(f"Failed to initialize Telegram Publisher: {e}")
        
        # Initialize WordPress publisher
        if wordpress_config:
            try:
                self.publishers['wordpress'] = WordPressPublisher(
                    site_url=wordpress_config['site_url'],
                    username=wordpress_config['username'],
                    app_password=wordpress_config['app_password']
                )
                logger.info("WordPress Publisher initialized")
            except Exception as e:
                logger.error(f"Failed to initialize WordPress Publisher: {e}")
    
    async def check_all_connections(self) -> Dict[str, bool]:
        """
        Check all platform connections
        
        Returns:
            Dictionary with platform: status
        """
        results = {}
        
        for platform, publisher in self.publishers.items():
            try:
                status = await publisher.check_connection()
                results[platform] = status
                logger.info(f"{platform.upper()}: {'✅ OK' if status else '❌ FAILED'}")
            except Exception as e:
                results[platform] = False
                logger.error(f"{platform.upper()}: ❌ ERROR - {e}")
        
        return results
    
    async def publish_post(
        self,
        post_id: int,
        platforms: List[str],
        region_code: str,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Publish post to specified platforms
        
        Args:
            post_id: Post ID from database
            platforms: List of platforms ('vk', 'telegram', 'wordpress')
            region_code: Region code for targeting
            **kwargs: Additional platform-specific parameters
            
        Returns:
            Dictionary with publishing results
        """
        async with AsyncSessionLocal() as session:
            # Get post
            result = await session.execute(
                select(Post).where(Post.id == post_id)
            )
            post = result.scalar_one_or_none()
            
            if not post:
                return {'error': f'Post {post_id} not found'}
            
            # Check if post is approved
            if post.status != 'approved':
                return {'error': f'Post {post_id} is not approved (status: {post.status})'}
            
            # Get region
            result = await session.execute(
                select(Region).where(Region.code == region_code)
            )
            region = result.scalar_one_or_none()
            
            if not region:
                return {'error': f'Region {region_code} not found'}
            
            # Publish to each platform
            results = {}
            
            for platform in platforms:
                if platform not in self.publishers:
                    results[platform] = {
                        'success': False,
                        'error': f'Publisher not initialized'
                    }
                    continue
                
                try:
                    # Get target ID for platform
                    target_id = None
                    if platform == 'vk':
                        target_id = str(region.vk_group_id) if region.vk_group_id else None
                    elif platform == 'telegram':
                        target_id = region.telegram_channel
                    
                    if not target_id:
                        results[platform] = {
                            'success': False,
                            'error': f'No target configured for region'
                        }
                        continue
                    
                    # Publish
                    result = await self.publishers[platform].publish_post(
                        post,
                        target_id,
                        **kwargs
                    )
                    
                    results[platform] = result
                    
                    # Update post status in database
                    if result.get('success'):
                        if platform == 'vk':
                            post.published_vk = True
                        elif platform == 'telegram':
                            post.published_telegram = True
                        elif platform == 'wordpress':
                            post.published_wordpress = True
                    
                except Exception as e:
                    logger.error(f"Error publishing to {platform}: {e}")
                    results[platform] = {
                        'success': False,
                        'error': str(e)
                    }
            
            # Update post status
            if any(r.get('success') for r in results.values()):
                post.status = 'published'
                post.published_at = datetime.utcnow()
            
            await session.commit()
            
            return {
                'post_id': post_id,
                'region': region_code,
                'results': results
            }
    
    async def publish_approved_posts(
        self,
        region_code: str,
        platforms: List[str],
        limit: int = 10
    ) -> Dict[str, Any]:
        """
        Publish all approved posts for a region
        
        Args:
            region_code: Region code
            platforms: List of platforms to publish to
            limit: Maximum number of posts to publish
            
        Returns:
            Publishing statistics
        """
        async with AsyncSessionLocal() as session:
            # Get region
            result = await session.execute(
                select(Region).where(Region.code == region_code)
            )
            region = result.scalar_one_or_none()
            
            if not region:
                return {'error': f'Region {region_code} not found'}
            
            # Get approved posts
            result = await session.execute(
                select(Post).where(
                    and_(
                        Post.region_id == region.id,
                        Post.status == 'approved',
                        Post.published_at.is_(None)
                    )
                ).order_by(Post.ai_score.desc()).limit(limit)
            )
            posts = result.scalars().all()
            
            if not posts:
                logger.info(f"No approved posts to publish for region {region_code}")
                return {
                    'region': region_code,
                    'published': 0,
                    'total': 0
                }
            
            logger.info(f"Publishing {len(posts)} posts for region {region_code}")
            
            published_count = 0
            failed_count = 0
            
            for post in posts:
                result = await self.publish_post(
                    post.id,
                    platforms,
                    region_code
                )
                
                if not result.get('error'):
                    success_count = sum(
                        1 for r in result['results'].values()
                        if r.get('success')
                    )
                    if success_count > 0:
                        published_count += 1
                    else:
                        failed_count += 1
                else:
                    failed_count += 1
                
                # Small delay between posts
                await asyncio.sleep(2)
            
            return {
                'region': region_code,
                'total': len(posts),
                'published': published_count,
                'failed': failed_count
            }
    
    async def get_publishing_stats(self) -> Dict[str, Any]:
        """
        Get publishing statistics
        
        Returns:
            Statistics dictionary
        """
        async with AsyncSessionLocal() as session:
            # Get counts
            result = await session.execute(
                select(Post).where(Post.status == 'published')
            )
            published_posts = result.scalars().all()
            
            vk_count = sum(1 for p in published_posts if p.published_vk)
            telegram_count = sum(1 for p in published_posts if p.published_telegram)
            wordpress_count = sum(1 for p in published_posts if p.published_wordpress)
            
            return {
                'total_published': len(published_posts),
                'vk': vk_count,
                'telegram': telegram_count,
                'wordpress': wordpress_count,
                'platforms_active': len(self.publishers)
            }

