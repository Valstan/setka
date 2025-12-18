"""
VK Monitor - scans VK communities for new posts
"""
import asyncio
import logging
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from database.connection import AsyncSessionLocal
from database.models import Community, Post, Region
from modules.vk_monitor.vk_client_async import VKClientAsync, VKTokenRotatorAsync
from modules.deduplication import (
    create_lip_fingerprint,
    create_media_fingerprint,
    create_text_fingerprint,
    create_text_core_fingerprint,
    DuplicationDetector
)
from modules.module_activity_notifier import (
    notify_vk_scan_started,
    notify_vk_scan_completed,
    notify_vk_posts_found
)

logger = logging.getLogger(__name__)


class VKMonitor:
    """Main VK monitoring class"""
    
    def __init__(self, vk_tokens: List[str]):
        """
        Initialize VK Monitor
        
        Args:
            vk_tokens: List of VK API tokens
        """
        self.token_rotator = VKTokenRotatorAsync(vk_tokens)
        self.running = False
    
    async def scan_community(
        self,
        community: Community,
        session: AsyncSession
    ) -> int:
        """
        Scan single community for new posts
        
        Args:
            community: Community object from database
            session: Database session
            
        Returns:
            Number of new posts found
        """
        client = await self.token_rotator.get_client()
        if not client:
            logger.error("No VK client available")
            return 0
        
        try:
            logger.info(f"Scanning community: {community.name} (ID: {community.vk_id})")
            
            # Use client in async context manager to ensure proper cleanup
            async with client:
                # Fetch posts from VK (async)
                posts = await client.get_wall_posts(
                    owner_id=community.vk_id,
                    count=10  # Get last 10 posts
                )
                
                new_posts_count = 0
                
                for vk_post in posts:
                    post_id = vk_post.get('id')
                    
                    # Check if post already exists
                    result = await session.execute(
                        select(Post).where(
                            and_(
                                Post.vk_owner_id == community.vk_id,
                                Post.vk_post_id == post_id
                            )
                        )
                    )
                    existing_post = result.scalar_one_or_none()
                    
                    if existing_post:
                        # Post already exists, update stats
                        stats = VKClientAsync.extract_post_stats(vk_post)
                        existing_post.views = stats['views']
                        existing_post.likes = stats['likes']
                        existing_post.reposts = stats['reposts']
                        existing_post.comments = stats['comments']
                        existing_post.updated_at = datetime.utcnow()
                        continue
                    
                    # Create new post
                    text = vk_post.get('text', '')
                    attachments = VKClientAsync.parse_attachments(vk_post)
                    stats = VKClientAsync.extract_post_stats(vk_post)
                    
                    # Get post date
                    date_timestamp = vk_post.get('date', 0)
                    date_published = datetime.fromtimestamp(date_timestamp) if date_timestamp else datetime.utcnow()
                    
                    # Create fingerprints (inspired by Postopus)
                    fingerprint_lip = create_lip_fingerprint(community.vk_id, post_id)
                    fingerprint_media = create_media_fingerprint(attachments) if attachments else None
                    fingerprint_text = create_text_fingerprint(text) if text else None
                    fingerprint_text_core = create_text_core_fingerprint(text) if text else None
                    
                    new_post = Post(
                        region_id=community.region_id,
                        community_id=community.id,
                        vk_post_id=post_id,
                        vk_owner_id=community.vk_id,
                        text=text,
                        attachments=attachments,
                        date_published=date_published,
                        views=stats['views'],
                        likes=stats['likes'],
                        reposts=stats['reposts'],
                        comments=stats['comments'],
                        status='new',
                        # Fingerprints for deduplication
                        fingerprint_lip=fingerprint_lip,
                        fingerprint_media=fingerprint_media,
                        fingerprint_text=fingerprint_text,
                        fingerprint_text_core=fingerprint_text_core
                    )
                    
                    session.add(new_post)
                    new_posts_count += 1
                    logger.info(f"New post found: {community.vk_id}_{post_id}")
                
                # Уведомляем о найденных постах
                if new_posts_count > 0:
                    notify_vk_posts_found(community.region.code if community.region else "unknown", 
                                        new_posts_count, community.name)
                
                # Update community stats
                community.last_checked = datetime.utcnow()
                if posts:
                    community.last_post_id = posts[0].get('id')
                community.posts_count += new_posts_count
                
                await session.commit()
                
                logger.info(f"Community {community.name}: {new_posts_count} new posts")
                return new_posts_count
            
        except Exception as e:
            logger.error(f"Error scanning community {community.name}: {e}")
            community.errors_count += 1
            community.last_checked = datetime.utcnow()
            await session.commit()
            return 0
    
    async def scan_region(self, region_code: str) -> Dict[str, int]:
        """
        Scan all communities in a region
        
        Args:
            region_code: Region code (e.g., 'mi', 'nolinsk')
            
        Returns:
            Dictionary with scan results
        """
        async with AsyncSessionLocal() as session:
            # Get region
            result = await session.execute(
                select(Region).where(Region.code == region_code)
            )
            region = result.scalar_one_or_none()
            
            if not region:
                logger.error(f"Region {region_code} not found")
                return {'error': 'Region not found'}
            
            # Get active communities in region
            result = await session.execute(
                select(Community).where(
                    and_(
                        Community.region_id == region.id,
                        Community.is_active == True
                    )
                )
            )
            communities = result.scalars().all()
            
            if not communities:
                logger.warning(f"No active communities found for region {region_code}")
                return {'communities': 0, 'new_posts': 0}
            
            # Уведомляем о начале сканирования
            notify_vk_scan_started(region_code, len(communities))
            
            total_new_posts = 0
            scanned_communities = 0
            
            for community in communities:
                new_posts = await self.scan_community(community, session)
                total_new_posts += new_posts
                scanned_communities += 1
                
                # Small delay between requests to avoid rate limits
                await asyncio.sleep(0.5)
            
            # Уведомляем о завершении сканирования
            notify_vk_scan_completed(region_code, total_new_posts, scanned_communities, 0.0)
            
            return {
                'region': region_code,
                'communities': scanned_communities,
                'new_posts': total_new_posts
            }
    
    async def scan_all_regions(self) -> Dict[str, Any]:
        """
        Scan all active regions
        
        Returns:
            Dictionary with overall scan results
        """
        async with AsyncSessionLocal() as session:
            # Get all active regions
            result = await session.execute(
                select(Region).where(Region.is_active == True)
            )
            regions = result.scalars().all()
            
            total_communities = 0
            total_new_posts = 0
            results_by_region = {}
            
            for region in regions:
                logger.info(f"Scanning region: {region.name}")
                result = await self.scan_region(region.code)
                
                if 'error' not in result:
                    total_communities += result.get('communities', 0)
                    total_new_posts += result.get('new_posts', 0)
                    results_by_region[region.code] = result
                
                # Delay between regions
                await asyncio.sleep(1)
            
            return {
                'timestamp': datetime.utcnow().isoformat(),
                'regions_scanned': len(results_by_region),
                'total_communities': total_communities,
                'total_new_posts': total_new_posts,
                'details': results_by_region
            }
    
    async def start_monitoring(self, interval_seconds: int = 300):
        """
        Start continuous monitoring
        
        Args:
            interval_seconds: Interval between scans (default 5 minutes)
        """
        self.running = True
        logger.info(f"Starting VK monitoring (interval: {interval_seconds}s)")
        
        while self.running:
            try:
                logger.info("=== Starting scan cycle ===")
                results = await self.scan_all_regions()
                logger.info(f"Scan completed: {results['total_new_posts']} new posts found")
                
                # Wait for next cycle
                await asyncio.sleep(interval_seconds)
                
            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}")
                await asyncio.sleep(60)  # Wait 1 minute on error
    
    def stop_monitoring(self):
        """Stop monitoring"""
        self.running = False
        logger.info("Stopping VK monitoring")

