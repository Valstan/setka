"""
Advertisement filter for parsing pipeline

Migrated from old_postopus bin/utils/is_advertisement.py
Detects commercial/advertising content using multiple signal levels.
"""
from modules.filters.base import BaseFilter, FilterResult
from utils.text_utils import is_advertisement


class AdvertisementFilter(BaseFilter):
    """
    Filters out advertisement posts.
    
    Detection levels:
    1. VK API marked_as_ads flag
    2. Legal advertising markers (#реклама, #ad, erid:, etc.)
    3. Commercial patterns scoring (prices, discounts, CTA, contacts)
    4. Suspicious attachments/links
    
    Skipped for 'reklama' theme (ads are expected there).
    """
    
    name = "advertisement_filter"
    description = "Detects and filters advertisement posts"
    
    # Commercial patterns with weights
    COMMERCIAL_PATTERNS = {
        2: [  # High weight
            r'цена[:\s]\d+',
            r'стоимость[:\s]\d+',
            r'скидка',
            r'распродажа',
            r'акция\s*[:\s]',
            r'\d+\s*руб',
            r'\d+\s*₽',
            r'бесплатно',
            r'дешево',
            r'недорого',
            r'купить',
            r'заказать',
        ],
        1: [  # Lower weight
            r'звоните[:\s]',
            r'пишите[:\s]',
            r'тел[.:]?\s*[\d+\-]',
            r'т[.:]?\s*[\d+\-]',
            r'моб[.:]?\s*[\d+\-]',
            r'whatsapp',
            r'telegram',
            r'подробности',
            r'узнать больше',
            r'переходите',
            r'подписывайтесь',
        ],
    }
    
    # Legal advertising markers (immediate rejection)
    LEGAL_MARKERS = [
        '#реклама', '#реклама',
        '#ad', '#ad',
        '#sponsored',
        '#партнёрство', '#партнерство',
        'erid:',
        'на правах рекламы',
    ]
    
    # Suspicious links
    SUSPICIOUS_LINKS = [
        'vk.com/ads',
        'target.vk.com',
        'ads.vk.com',
    ]
    
    # Score threshold for rejection
    SCORE_THRESHOLD = 4
    
    async def apply(self, post_data: dict, context: dict) -> FilterResult:
        """
        Check if post is advertisement.

        Args:
            post_data: VK post data
            context: Filter context (includes theme, region, etc.)

        Returns:
            FilterResult with accept/reject decision
        """
        theme = context.get('theme', '')
        text = post_data.get('text', '') or ''
        marked_as_ads = post_data.get('marked_as_ads', False)

        # Skip ad detection for reklama theme
        if theme == 'reklama':
            self.update_stats(FilterResult(passed=True))
            return FilterResult(passed=True, metadata={'score': 0})

        # Level 1: VK API flag
        if marked_as_ads:
            result = FilterResult(passed=False, reason="VK API marked as ads")
            self.update_stats(result)
            return result

        # Level 2: Legal advertising markers
        text_lower = text.lower()
        for marker in self.LEGAL_MARKERS:
            if marker.lower() in text_lower:
                result = FilterResult(passed=False, reason=f"Legal ad marker found: '{marker}'")
                self.update_stats(result)
                return result

        # Level 3: Commercial patterns scoring
        score = self._calculate_commercial_score(text_lower)

        if score >= self.SCORE_THRESHOLD:
            result = FilterResult(
                passed=False,
                reason=f"Commercial score too high: {score} (threshold: {self.SCORE_THRESHOLD})",
                metadata={'score': score}
            )
            self.update_stats(result)
            return result

        # Level 4: Suspicious links
        for link in self.SUSPICIOUS_LINKS:
            if link in text_lower:
                score += 1

        if score >= self.SCORE_THRESHOLD:
            result = FilterResult(
                passed=False,
                reason=f"Suspicious ads links found, score: {score}",
                metadata={'score': score}
            )
            self.update_stats(result)
            return result

        # Not an ad
        self.update_stats(FilterResult(passed=True))
        return FilterResult(passed=True, metadata={'score': score})
    
    def _calculate_commercial_score(self, text_lower: str) -> int:
        """Calculate commercial patterns score."""
        import re
        
        score = 0
        for weight, patterns in self.COMMERCIAL_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, text_lower):
                    score += weight
        
        return score
