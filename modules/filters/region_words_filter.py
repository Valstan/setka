"""
Region words filter for parsing pipeline

Migrated from old_postopus region words logic (kirov_words, tatar_words)
Ensures posts contain region-specific keywords when required.
"""
from modules.filters.base import BaseFilter, FilterResult
from utils.text_utils import search_text


class RegionWordsFilter(BaseFilter):
    """
    Filters posts that don't contain required region-specific words.
    
    In old_postopus, certain groups (filter_group_by_region_words) require
    posts to contain specific keywords for that region.
    
    This prevents irrelevant content from being aggregated.
    """
    
    name = "region_words_filter"
    description = "Ensures posts contain region-specific keywords"
    
    async def apply(self, post_data: dict, context: dict) -> FilterResult:
        """
        Check if post contains required region words.
        
        Args:
            post_data: VK post data
            context: Filter context with:
                - region_config: RegionConfig object
                - community_vk_id: VK ID of community being parsed
                - theme: Current theme
        
        Returns:
            FilterResult with accept/reject decision
        """
        region_config = context.get('region_config')
        community_vk_id = context.get('community_vk_id')
        text = post_data.get('text', '') or ''
        
        if not region_config or not community_vk_id:
            # No config or community, allow by default
            self.stats['accepted'] += 1
            return FilterResult.accept(self.name, reason="No region config or community ID")
        
        # Check if this community requires region words
        filter_groups = region_config.filter_group_by_region_words or {}
        
        # Convert community_vk_id to string for lookup
        community_id_str = str(abs(community_vk_id))
        
        # Find matching filter group
        required_words = None
        for group_id, words in filter_groups.items():
            if str(abs(int(group_id))) == community_id_str:
                required_words = words
                break
        
        if not required_words:
            # This community doesn't require region words
            self.stats['accepted'] += 1
            return FilterResult.accept(self.name, reason="Community doesn't require region words")
        
        # Check if text contains ANY of the required words
        text_lower = text.lower()
        found_words = []
        
        for word in required_words:
            word_lower = word.lower()
            if word_lower in text_lower:
                found_words.append(word)
        
        if found_words:
            # Found region words
            self.stats['accepted'] += 1
            return FilterResult.accept(
                self.name,
                reason=f"Found region words: {', '.join(found_words[:3])}",
                metadata={'found_words': found_words}
            )
        
        # No region words found - reject
        self.stats['rejected'] += 1
        return FilterResult.reject(
            self.name,
            reason=f"No region words found (required: {', '.join(required_words[:5])})",
            severity='medium',
            metadata={'required_words': required_words[:10]}
        )
    
    def check_post_for_region_words(
        self,
        text: str,
        required_words: list,
        min_matches: int = 1
    ) -> bool:
        """
        Check if text contains minimum number of required words.
        
        Args:
            text: Post text
            required_words: List of required words/phrases
            min_matches: Minimum number of words that must match
        
        Returns:
            True if minimum matches found
        """
        if not text or not required_words:
            return min_matches == 0
        
        text_lower = text.lower()
        match_count = 0
        
        for word in required_words:
            if word.lower() in text_lower:
                match_count += 1
                if match_count >= min_matches:
                    return True
        
        return False
