"""
Event Distribution Module (karavan)

Migrated from old_postopus bin/control/karavan.py
Distributes music/cultural events to all regions.

Concept: Central event repository -> broadcast to all regional channels
"""
import logging
from typing import List, Dict, Any

logger = logging.getLogger(__name__)


class KaravanEventDistributor:
    """
    Distributes events to all regions.
    
    Migrated from old_postopus karavan.py:
    - Fetches events from central source
    - Formats for each region
    - Publishes to all regional channels
    
    Used for music events, cultural events that are relevant across all regions.
    """
    
    def __init__(self):
        self.stats = {
            'events_processed': 0,
            'regions_published': 0,
            'total_posts': 0,
        }
    
    def format_event_for_region(
        self,
        event_data: Dict[str, Any],
        region_code: str,
        region_name: str,
        header: str = "",
        hashtag: str = "",
    ) -> Dict[str, Any]:
        """
        Format event post for specific region.
        
        Args:
            event_data: Event post data
            region_code: Region code
            region_name: Region display name
            header: Regional header
            hashtag: Regional hashtag
        
        Returns:
            Formatted post data
        """
        text = event_data.get('text', '') or ''
        
        # Add regional header
        formatted_parts = []
        
        if header:
            formatted_parts.append(header)
            formatted_parts.append("")
        
        # Event details
        formatted_parts.append(text)
        formatted_parts.append("")
        
        # Add attribution
        owner_id = event_data.get('owner_id', event_data.get('from_id', 0))
        post_id = event_data.get('id', 0)
        formatted_parts.append(f"📍 Источник: https://vk.com/wall{owner_id}_{post_id}")
        
        # Add regional hashtag
        if hashtag:
            tag = hashtag if hashtag.startswith('#') else f"#{hashtag}"
            formatted_parts.append(tag)
        
        return {
            **event_data,
            'text': "\n".join(formatted_parts),
        }
    
    def distribute_event_to_all_regions(
        self,
        event_data: Dict[str, Any],
        regions: List[Dict[str, str]],
    ) -> List[Dict[str, Any]]:
        """
        Prepare event for distribution to all regions.
        
        Args:
            event_data: Event post data
            regions: List of {code, name, header, hashtag}
        
        Returns:
            List of formatted posts for each region
        """
        formatted_posts = []
        
        for region in regions:
            formatted = self.format_event_for_region(
                event_data,
                region_code=region['code'],
                region_name=region['name'],
                header=region.get('header', ''),
                hashtag=region.get('hashtag', ''),
            )
            formatted_posts.append(formatted)
            self.stats['regions_published'] += 1
        
        self.stats['events_processed'] += 1
        self.stats['total_posts'] += len(formatted_posts)
        
        logger.info(f"🎪 Distributed event to {len(formatted_posts)} regions")
        
        return formatted_posts
    
    def get_stats(self) -> Dict[str, Any]:
        """Get distribution statistics."""
        return self.stats.copy()
