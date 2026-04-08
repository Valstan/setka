"""
Cross-Region Repost Module

Migrated from old_postopus repost_oleny.py and repost_me.py
Handles reposting content from one region to another.

repost_oleny: Cross-posts from Gonba region to all other regions
repost_me: Reposts from own regional groups using wall.repost
"""
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)


class CrossRegionRepost:
    """
    Manages cross-region reposting.
    
    Migrated from old_postopus:
    - repost_oleny.py: Gonba -> all other regions
    - repost_me.py: Own groups reposting
    
    Features:
    - Fetches from source region
    - Formats for target regions
    - Uses VK wall.repost API
    """
    
    def __init__(self, vk_publisher):
        """
        Args:
            vk_publisher: VKPublisher instance
        """
        self.vk_publisher = vk_publisher
        self.stats = {
            'source_posts_scanned': 0,
            'reposts_published': 0,
            'regions_updated': 0,
        }
    
    async def repost_from_source_to_regions(
        self,
        source_owner_id: int,
        source_post_id: int,
        target_regions: List[Dict[str, Any]],
        message: str = "",
    ) -> List[Dict[str, Any]]:
        """
        Repost single source post to multiple regions.
        
        Args:
            source_owner_id: Source post owner ID
            source_post_id: Source post ID
            target_regions: List of {group_id, region_code}
            message: Optional message to add
        
        Returns:
            List of publish results
        """
        results = []
        
        for region in target_regions:
            group_id = region['group_id']
            region_code = region['region_code']
            
            result = await self.vk_publisher.publish_repost(
                group_id=group_id,
                source_owner_id=source_owner_id,
                source_post_id=source_post_id,
                message=message,
            )
            
            result['region_code'] = region_code
            results.append(result)
            
            if result.get('success'):
                self.stats['reposts_published'] += 1
                self.stats['regions_updated'] += 1
            
            logger.info(
                f"{'✅' if result.get('success') else '❌'} "
                f"Repost to {region_code}: {result.get('url', result.get('error'))}"
            )
        
        self.stats['source_posts_scanned'] += 1
        
        return results
    
    def select_posts_for_repost(
        self,
        posts: List[Dict[str, Any]],
        min_engagement_score: float = 0.5,
    ) -> List[Dict[str, Any]]:
        """
        Select posts suitable for cross-region reposting.
        
        Args:
            posts: List of VK post data
            min_engagement_score: Minimum engagement to qualify
        
        Returns:
            Filtered posts
        """
        from utils.post_utils import post_popularity
        
        suitable = []
        
        for post in posts:
            # Calculate engagement
            score = post_popularity(
                views=post.get('views', 0),
                likes=post.get('likes', {}).get('count', 0),
                comments=post.get('comments', {}).get('count', 0),
                reposts=post.get('reposts', {}).get('count', 0),
            )
            
            # Filter by engagement
            if score >= min_engagement_score:
                post['_engagement_score'] = score
                suitable.append(post)
        
        # Sort by engagement (highest first)
        suitable.sort(key=lambda p: p.get('_engagement_score', 0), reverse=True)
        
        return suitable
    
    def get_stats(self) -> Dict[str, Any]:
        """Get repost statistics."""
        return self.stats.copy()
