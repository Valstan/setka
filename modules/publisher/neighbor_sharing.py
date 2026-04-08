"""
Neighbor Sharing Module (sosed)

Migrated from old_postopus bin/control/sosed.py
Fetches news from neighboring regions with #Новости hashtag and publishes locally.

Concept: Regional cross-pollination - share important news between neighboring districts.
"""
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime

from database.models_extended import RegionConfig
from utils.post_utils import lip_of_post, url_of_post
from utils.text_utils import search_text

logger = logging.getLogger(__name__)


class NeighborSharing:
    """
    Manages news sharing between neighboring regions.
    
    Migrated from old_postopus sosed.py logic:
    - Gets neighbor region list from RegionConfig.sosed
    - Fetches posts from neighbor communities
    - Filters for #Новости hashtag (required)
    - Publishes to local region
    
    This ensures only important news (with #Новости) gets shared.
    """
    
    # Required hashtag for neighbor sharing
    REQUIRED_HASHTAG = "#Новости"
    
    def __init__(self):
        self.stats = {
            'neighbors_checked': 0,
            'posts_scanned': 0,
            'posts_with_hashtag': 0,
            'posts_published': 0,
        }
    
    def get_neighbor_regions(self, region_config: RegionConfig) -> List[str]:
        """
        Get list of neighbor region codes from config.
        
        Args:
            region_config: RegionConfig with sosed field
        
        Returns:
            List of neighbor region codes
        """
        sosed_str = region_config.sosed or ""
        
        if not sosed_str.strip():
            return []
        
        # Parse comma or space-separated region names
        # Example: "Малмыж - Инфо,Уржум - Инфо"
        neighbor_names = [name.strip() for name in sosed_str.replace(',', ' ').split() if name.strip()]
        
        # Map region names to codes
        from scripts.migrate_mongodb_config import REGION_MAPPING
        reverse_mapping = {v: k for k, v in REGION_MAPPING.items()}
        
        neighbor_codes = []
        for code, name in REGION_MAPPING.items():
            if name in neighbor_names:
                neighbor_codes.append(code)
        
        return neighbor_codes
    
    def should_share_with_neighbors(self, post_data: Dict[str, Any]) -> bool:
        """
        Check if post should be shared with neighbors.
        
        In old_postopus, only posts with #Новости hashtag were shared.
        
        Args:
            post_data: VK post data
        
        Returns:
            True if post should be shared
        """
        text = post_data.get('text', '') or ''
        
        # Check for required hashtag
        if self.REQUIRED_HASHTAG.lower() not in text.lower():
            return False
        
        # Additional checks can be added here:
        # - Minimum quality score
        # - No advertisements
        # - Has media attachments
        
        return True
    
    def filter_posts_for_sharing(
        self,
        posts: List[Dict[str, Any]],
        region_config: RegionConfig,
    ) -> List[Dict[str, Any]]:
        """
        Filter posts that are suitable for neighbor sharing.
        
        Args:
            posts: List of VK post data
            region_config: RegionConfig
        
        Returns:
            Filtered list of posts
        """
        suitable_posts = []
        
        for post in posts:
            self.stats['posts_scanned'] += 1
            
            # Check for required hashtag
            text = post.get('text', '') or ''
            if self.REQUIRED_HASHTAG.lower() not in text.lower():
                continue
            
            self.stats['posts_with_hashtag'] += 1
            
            # Check not an ad (simplified - would use full ad filter in production)
            if self._is_advertisement(text):
                continue
            
            # Suitable for sharing
            suitable_posts.append(post)
        
        self.stats['neighbors_checked'] += 1
        
        return suitable_posts
    
    async def collect_neighbor_news(
        self,
        region_config: RegionConfig,
        vk_monitor,
        max_posts_per_neighbor: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        Collect news from neighboring regions.
        
        Args:
            region_config: RegionConfig
            vk_monitor: VKMonitor instance to fetch posts
            max_posts_per_neighbor: Max posts to collect from each neighbor
        
        Returns:
            List of neighbor posts suitable for sharing
        """
        neighbor_codes = self.get_neighbor_regions(region_config)
        
        if not neighbor_codes:
            logger.info(f"No neighbors configured for {region_config.region_code}")
            return []
        
        all_neighbor_posts = []
        
        for neighbor_code in neighbor_codes:
            logger.info(f"📡 Fetching news from neighbor: {neighbor_code}")
            
            # Get neighbor communities
            # This would use the existing VK monitor infrastructure
            try:
                # Fetch recent posts from neighbor's communities
                # Implementation depends on how vk_monitor exposes this
                posts = await vk_monitor.get_recent_posts_for_region(
                    neighbor_code,
                    limit=max_posts_per_neighbor,
                )
                
                # Filter for sharing
                suitable = self.filter_posts_for_sharing(posts, region_config)
                all_neighbor_posts.extend(suitable)
                
                logger.info(f"  ✅ Collected {len(suitable)} posts from {neighbor_code}")
                
            except Exception as e:
                logger.error(f"  ❌ Failed to fetch from {neighbor_code}: {e}")
        
        logger.info(f"📊 Total neighbor posts collected: {len(all_neighbor_posts)}")
        return all_neighbor_posts
    
    def format_for_neighbor_sharing(
        self,
        post_data: Dict[str, Any],
        local_region_config: RegionConfig,
    ) -> Dict[str, Any]:
        """
        Format post for publishing in local region.
        
        Adds attribution and local hashtags.
        
        Args:
            post_data: Original post data
            local_region_config: Target region config
        
        Returns:
            Formatted post data
        """
        text = post_data.get('text', '') or ''
        
        # Add source attribution
        owner_id = post_data.get('owner_id', post_data.get('from_id', 0))
        post_id = post_data.get('id', 0)
        post_url = url_of_post(owner_id, post_id)
        
        attribution = f"\n\n📰 Источник: {post_url}"
        
        # Add local hashtag
        local_tag = local_region_config.heshteg_local or {}
        local_hashtag = f"#{local_tag.get('raicentr', '')}" if local_tag else ""
        
        # Build final text
        formatted_text = text + attribution
        
        if local_hashtag:
            formatted_text += f"\n{local_hashtag}"
        
        # Return modified copy
        return {
            **post_data,
            'text': formatted_text,
            'formatted_for_neighbor_sharing': True,
        }
    
    def _is_advertisement(self, text: str) -> bool:
        """Quick ad check for neighbor posts."""
        ad_markers = [
            '#реклама', '#ad', 'erid:',
            'цена', 'скидка', 'купить',
        ]
        
        text_lower = text.lower()
        return any(marker in text_lower for marker in ad_markers)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get neighbor sharing statistics."""
        return self.stats.copy()
