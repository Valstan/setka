"""
Text duplicate filter for parsing pipeline

Migrated from old_postopus text duplicate detection logic.
Uses normalized text fingerprinting (text_to_rafinad) with core slice comparison.
"""
from modules.filters.base import BaseFilter, FilterResult
from utils.text_utils import text_to_rafinad
from utils.post_utils import lip_of_post


class TextDuplicateFilter(BaseFilter):
    """
    Filters posts with duplicate text content.
    
    Migrated from old_postopus logic:
    - Normalizes text via text_to_rafinad() (strips all non-word chars)
    - Takes core slice (20%-70% for novost, 35%-55% for thematic)
    - Compares against recently published posts
    
    This prevents publishing near-identical content across regions.
    """
    
    name = "text_duplicate_filter"
    description = "Detects duplicate text content using normalized fingerprinting"
    
    # Core slice percentages (start%, end%)
    CORE_SLICES = {
        'novost': (20, 70),    # Wider slice for news
        'reklama': (20, 70),   # Same for ads
        'default': (35, 55),   # Narrower for thematic (reduces false positives)
    }
    
    # Minimum text length to check
    MIN_TEXT_LENGTH = 50
    
    async def apply(self, post_data: dict, context: dict) -> FilterResult:
        """
        Check if post text is a duplicate.
        
        Args:
            post_data: VK post data
            context: Filter context with:
                - region_code: Region code
                - theme: Current theme
                - recent_fingerprints: Set of recent text fingerprints
                - work_table_lip: List of recently published post lips
        
        Returns:
            FilterResult with accept/reject decision
        """
        text = post_data.get('text', '') or ''
        theme = context.get('theme', 'default')
        recent_fingerprints = context.get('recent_fingerprints', set())
        
        # Skip if text is too short
        if len(text) < self.MIN_TEXT_LENGTH:
            self.stats['accepted'] += 1
            return FilterResult.accept(
                self.name,
                reason=f"Text too short ({len(text)} chars) for duplicate check"
            )
        
        # Normalize text
        normalized = text_to_rafinad(text)
        
        if len(normalized) < self.MIN_TEXT_LENGTH:
            self.stats['accepted'] += 1
            return FilterResult.accept(
                self.name,
                reason=f"Normalized text too short ({len(normalized)} chars)"
            )
        
        # Get core slice for this theme
        start_pct, end_pct = self.CORE_SLICES.get(theme, self.CORE_SLICES['default'])
        
        start_idx = len(normalized) * start_pct // 100
        end_idx = len(normalized) * end_pct // 100
        
        core_text = normalized[start_idx:end_idx]
        
        if len(core_text) < 20:
            # Core slice too small, allow
            self.stats['accepted'] += 1
            return FilterResult.accept(self.name, reason="Core slice too small")
        
        # Check against recent fingerprints
        # In production, this would compare against stored fingerprints
        for fingerprint in recent_fingerprints:
            if core_text in fingerprint or fingerprint in core_text:
                self.stats['rejected'] += 1
                return FilterResult.reject(
                    self.name,
                    reason=f"Text duplicate detected (core slice: {start_pct}%-{end_pct}%)",
                    severity='high',
                    metadata={
                        'core_length': len(core_text),
                        'slice': f"{start_pct}%-{end_pct}%",
                    }
                )
        
        # No duplicate found
        self.stats['accepted'] += 1
        return FilterResult.accept(
            self.name,
            metadata={
                'core_length': len(core_text),
                'slice': f"{start_pct}%-{end_pct}%",
            }
        )
    
    def compute_text_fingerprint(self, text: str) -> str:
        """
        Compute full text fingerprint (for storage).
        
        Returns normalized text hash for deduplication.
        """
        normalized = text_to_rafinad(text)
        
        # Return first 100 chars as fingerprint
        return normalized[:100]
    
    def compute_core_fingerprint(self, text: str, theme: str = 'default') -> str:
        """
        Compute core text fingerprint (slice comparison).
        
        Returns normalized core slice for deduplication.
        """
        normalized = text_to_rafinad(text)
        
        start_pct, end_pct = self.CORE_SLICES.get(theme, self.CORE_SLICES['default'])
        
        start_idx = len(normalized) * start_pct // 100
        end_idx = len(normalized) * end_pct // 100
        
        return normalized[start_idx:end_idx]
    
    def texts_are_similar(self, text1: str, text2: str, threshold: float = 0.8) -> bool:
        """
        Check if two texts are similar (for manual comparison).
        
        Args:
            text1: First text
            text2: Second text
            threshold: Similarity threshold (0.0-1.0)
        
        Returns:
            True if texts are similar
        """
        norm1 = text_to_rafinad(text1)
        norm2 = text_to_rafinad(text2)
        
        if not norm1 or not norm2:
            return False
        
        # Simple containment check
        shorter = norm1 if len(norm1) < len(norm2) else norm2
        longer = norm2 if len(norm1) < len(norm2) else norm1
        
        if shorter in longer:
            return True
        
        # Character-level similarity (quick approximation)
        common_chars = sum(1 for c in shorter if c in longer)
        similarity = common_chars / max(len(shorter), 1)
        
        return similarity >= threshold
