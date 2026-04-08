"""
Age filter for parsing pipeline

Migrated from old_postopus bin/sort/sort_old_date.py
Filters posts that are too old based on theme-specific thresholds.
"""
import time
from datetime import datetime, timezone
from modules.filters.base import BaseFilter, FilterResult


class AgeFilter(BaseFilter):
    """
    Filters posts that are too old.
    
    Age thresholds (from old_postopus):
    - hard: 24 hours (86400 seconds) - for news
    - medium: 48 hours (172800 seconds) - for thematic content
    - light: 7 days (604800 seconds) - for evergreen content
    
    Thresholds are configured per theme via RegionConfig.time_old_post.
    """
    
    name = "age_filter"
    description = "Filters posts that are too old based on theme thresholds"
    
    # Default thresholds (seconds)
    DEFAULT_THRESHOLDS = {
        'hard': 86400,      # 24 hours
        'medium': 172800,   # 48 hours
        'light': 604800,    # 7 days
    }
    
    # Theme to threshold mapping
    THEME_THRESHOLDS = {
        'novost': 'hard',
        'kultura': 'medium',
        'sport': 'medium',
        'detsad': 'light',
        'admin': 'light',
        'union': 'light',
        'reklama': 'medium',
        'sosed': 'hard',
    }
    
    async def apply(self, post_data: dict, context: dict) -> FilterResult:
        """
        Check if post is too old.
        
        Args:
            post_data: VK post data with 'date' field (Unix timestamp)
            context: Filter context (includes theme, region_config, etc.)
        
        Returns:
            FilterResult with accept/reject decision
        """
        theme = context.get('theme', 'novost')
        region_config = context.get('region_config')
        
        # Get post date
        post_date = post_data.get('date')
        if not post_date:
            # No date available, allow by default
            self.stats['accepted'] += 1
            return FilterResult.accept(self.name, reason="No date available")
        
        # Convert Unix timestamp to datetime if needed
        if isinstance(post_date, (int, float)):
            post_datetime = datetime.fromtimestamp(post_date, tz=timezone.utc)
        else:
            post_datetime = post_date
        
        # Calculate age in seconds
        now = datetime.now(tz=timezone.utc)
        age_seconds = (now - post_datetime).total_seconds()
        
        # Get threshold for this theme
        threshold_key = self.THEME_THRESHOLDS.get(theme, 'medium')
        
        # Use custom thresholds from region config if available
        if region_config and region_config.time_old_post:
            thresholds = region_config.time_old_post
        else:
            thresholds = self.DEFAULT_THRESHOLDS
        
        max_age = thresholds.get(threshold_key, self.DEFAULT_THRESHOLDS['medium'])
        
        # Check if post is too old
        if age_seconds > max_age:
            age_hours = age_seconds / 3600
            max_age_hours = max_age / 3600
            
            self.stats['rejected'] += 1
            return FilterResult.reject(
                self.name,
                reason=f"Post too old: {age_hours:.1f}h > {max_age_hours:.1f}h (threshold: {threshold_key})",
                severity='low',
                metadata={
                    'age_seconds': age_seconds,
                    'max_age_seconds': max_age,
                    'threshold_key': threshold_key,
                }
            )
        
        # Post is fresh enough
        self.stats['accepted'] += 1
        return FilterResult.accept(
            self.name,
            metadata={
                'age_seconds': age_seconds,
                'threshold_key': threshold_key,
            }
        )
    
    def get_threshold_for_theme(self, theme: str) -> int:
        """Get age threshold in seconds for a theme."""
        threshold_key = self.THEME_THRESHOLDS.get(theme, 'medium')
        return self.DEFAULT_THRESHOLDS.get(threshold_key, 172800)
