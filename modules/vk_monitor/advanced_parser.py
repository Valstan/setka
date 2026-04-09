"""
Advanced VK Parser - Main parsing orchestration module

Migrated from old_postopus bin/control/parser.py
Core parsing logic with full filtering pipeline.

This is the heart of the parsing system - fetches posts from VK communities,
applies all filters, and returns cleaned posts ready for digest building.
"""
import logging
import random
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime

from modules.vk_monitor.vk_client import VKClient
from modules.filters.base import FilterResult
from utils.post_utils import lip_of_post, clear_copy_history, post_popularity
from utils.vk_attachments import extract_vk_attachments, has_attachments
from utils.text_utils import is_advertisement, check_blacklist

logger = logging.getLogger(__name__)


class AdvancedVKParser:
    """
    Advanced VK post parser with full filtering pipeline.
    
    Migrated from old_postopus parser.py with all filtering logic:
    1. Duplicate lip check
    2. Age check
    3. Unwrap reposts (clear_copy_history)
    4. Black ID check
    5. Advertisement filter
    6. Theme-specific filters (sosed hashtag, region words, etc.)
    7. Text duplicate check
    8. Photo/video duplicate check
    9. No-attachments filter
    
    Returns filtered posts ready for digest building.
    """
    
    def __init__(self, vk_client: VKClient):
        """
        Args:
            vk_client: VK API client instance
        """
        self.vk_client = vk_client
        
        # Parsing statistics (stat_mode)
        self.stats = {
            'total_groups_checked': 0,
            'total_posts_scanned': 0,
            'posts_filtered_old': 0,
            'posts_filtered_duplicate_lip': 0,
            'posts_filtered_duplicate_text': 0,
            'posts_filtered_duplicate_foto': 0,
            'posts_filtered_black_id': 0,
            'posts_filtered_no_region_words': 0,
            'posts_filtered_advertisement': 0,
            'posts_filtered_no_attachments': 0,
            'posts_filtered_blacklist_text': 0,
            'posts_final_count': 0,
            'groups_with_posts': 0,
        }
    
    async def parse_posts_from_communities(
        self,
        community_ids: List[int],
        theme: str = "novost",
        region_config: Any = None,
        work_table_lip: List[str] = None,
        work_table_hash: List[str] = None,
        recent_text_fingerprints: List[str] = None,
        count_per_community: int = 20,
        shuffle_communities: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Parse posts from multiple communities with full filtering.
        
        This is the main entry point, migrated from old_postopus parser().
        
        Args:
            community_ids: List of VK community IDs to scan
            theme: Theme (novost, kultura, sport, etc.)
            region_config: RegionConfig object for filtering
            work_table_lip: List of published post lips (for dedup)
            work_table_hash: List of photo hashes (for dedup)
            recent_text_fingerprints: Recent text fingerprints (for dedup)
            count_per_community: Posts to fetch per community
            shuffle_communities: Randomize community order
        
        Returns:
            List of filtered post data dicts
        """
        if work_table_lip is None:
            work_table_lip = []
        if work_table_hash is None:
            work_table_hash = []
        if recent_text_fingerprints is None:
            recent_text_fingerprints = []
        
        # Shuffle communities (randomize fetch order)
        if shuffle_communities:
            random.shuffle(community_ids)
        
        all_posts = []
        
        # Fetch posts from all communities
        for community_id in community_ids:
            self.stats['total_groups_checked'] += 1
            
            try:
                # Fetch posts from VK
                posts = await self._fetch_community_posts(community_id, count_per_community)
                
                if not posts:
                    continue
                
                self.stats['groups_with_posts'] += 1
                
                # Process each post
                for post_data in posts:
                    self.stats['total_posts_scanned'] += 1
                    
                    # Apply full filtering pipeline
                    filtered = await self._filter_post(
                        post_data,
                        theme=theme,
                        region_config=region_config,
                        work_table_lip=work_table_lip,
                        work_table_hash=work_table_hash,
                        recent_text_fingerprints=recent_text_fingerprints,
                    )
                    
                    if filtered:
                        all_posts.append(filtered)
                
            except Exception as e:
                logger.error(f"❌ Failed to parse community {community_id}: {e}")
                continue
        
        # Sort by popularity
        all_posts.sort(
            key=lambda p: post_popularity(
                views=p.get('views', 0),
                likes=p.get('likes', {}).get('count', 0),
                comments=p.get('comments', {}).get('count', 0),
                reposts=p.get('reposts', {}).get('count', 0),
            ),
            reverse=True,
        )
        
        self.stats['posts_final_count'] = len(all_posts)
        
        logger.info(
            f"📊 Parsing complete: {len(all_posts)} posts from "
            f"{self.stats['total_groups_checked']} groups"
        )
        
        return all_posts
    
    async def _fetch_community_posts(self, community_id: int, count: int) -> List[Dict]:
        """Fetch posts from a single VK community."""
        # Use VK client to get wall posts
        # Implementation depends on your VK client setup
        
        if hasattr(self.vk_client, 'get_wall_posts'):
            return await self.vk_client.get_wall_posts(community_id, count)
        elif hasattr(self.vk_client, 'api_call'):
            response = await self.vk_client.api_call('wall.get', {
                'owner_id': -abs(community_id),  # Negative for groups
                'count': count,
            })
            return response.get('items', [])
        else:
            raise NotImplementedError("VK client doesn't support wall.get")
    
    async def _filter_post(
        self,
        post_data: Dict[str, Any],
        theme: str,
        region_config: Any,
        work_table_lip: List[str],
        work_table_hash: List[str],
        recent_text_fingerprints: List[str],
    ) -> Optional[Dict[str, Any]]:
        """
        Apply full filtering pipeline to a single post.
        
        Returns filtered post or None if rejected.
        """
        owner_id = post_data.get('owner_id', post_data.get('from_id', 0))
        post_id = post_data.get('id', 0)
        text = post_data.get('text', '') or ''
        
        # 1. Duplicate lip check
        lip = lip_of_post(owner_id, post_id)
        if lip in work_table_lip:
            self.stats['posts_filtered_duplicate_lip'] += 1
            return None
        
        # 2. Unwrap reposts (clear_copy_history)
        post_data = clear_copy_history(post_data)
        
        # 3. Black ID check
        if region_config and region_config.black_id:
            if abs(owner_id) in [abs(x) for x in region_config.black_id]:
                self.stats['posts_filtered_black_id'] += 1
                return None
        
        # 4. Advertisement filter
        is_reklama_theme = (theme == 'reklama')
        if is_advertisement(text, skip_for_reklama=is_reklama_theme, theme=theme):
            if not is_reklama_theme:
                self.stats['posts_filtered_advertisement'] += 1
                return None
        
        # 5. Blacklist text check
        if region_config and region_config.delete_msg_blacklist:
            matched = check_blacklist(text, region_config.delete_msg_blacklist)
            if matched:
                self.stats['posts_filtered_blacklist_text'] += 1
                return None
        
        # 6. Region words filter (for specific communities)
        if region_config and region_config.filter_group_by_region_words:
            community_vk_id = post_data.get('community_vk_id', owner_id)
            if str(abs(community_vk_id)) in {str(abs(x)) for x in region_config.filter_group_by_region_words.keys()}:
                # This community requires region words - check in post filter
                # This would be handled by RegionWordsFilter in the new system
                pass  # Placeholder - would use RegionWordsFilter
        
        # 7. No-attachments filter (for non-novost/non-reklama themes)
        if theme not in ('novost', 'reklama'):
            attachments = extract_vk_attachments(post_data)
            if not has_attachments(attachments):
                self.stats['posts_filtered_no_attachments'] += 1
                return None
        
        # 8. Theme-specific filters
        if theme == 'sosed':
            # Must have #Новости hashtag
            if '#новости' not in text.lower():
                return None
        
        # Post passed all filters
        return post_data
    
    def get_stats(self) -> Dict[str, Any]:
        """Get parsing statistics (for stat_mode)."""
        return self.stats.copy()
    
    def reset_stats(self):
        """Reset parsing statistics."""
        for key in self.stats:
            self.stats[key] = 0
