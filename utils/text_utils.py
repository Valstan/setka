"""
Text utilities migrated from old_postopus bin/utils/

Provides text cleaning, normalization, and search utilities
used by the parsing and filtering pipeline.
"""
import re
from typing import List, Optional


def text_to_rafinad(text: str) -> str:
    """
    Strips all non-word characters from text.
    Equivalent to old_postopus bin/utils/text_to_rafinad.py
    
    Used for text deduplication - normalizes text before fingerprint comparison.
    """
    if not text:
        return ""
    # Keep only word characters (letters, digits, underscore)
    return re.sub(r'[^\w]', '', text, flags=re.UNICODE)


def clear_text(text: str, blacklist: Optional[List[str]] = None) -> str:
    """
    Clean text using regex patterns from blacklist.
    Migrated from old_postopus clear_text.py
    
    Args:
        text: Input text
        blacklist: List of regex patterns to remove
    
    Returns:
        Cleaned text
    """
    if not text:
        return ""
    
    if not blacklist:
        # Default patterns
        blacklist = [
            r'#\w+',  # hashtags
            r'@\w+',  # mentions
            r'http\S+',  # URLs
        ]
    
    cleaned = text
    for pattern in blacklist:
        cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE | re.MULTILINE)
    
    return cleaned.strip()


def search_text(pattern: str, text: str) -> bool:
    """
    Case-insensitive regex search in text.
    Migrated from old_postopus bin/utils/search_text.py
    
    Args:
        pattern: Regex pattern to search for
        text: Text to search in
    
    Returns:
        True if pattern found
    """
    if not pattern or not text:
        return False
    
    try:
        return bool(re.search(pattern, text, re.IGNORECASE | re.MULTILINE))
    except re.error:
        return False


def is_advertisement(text: str, skip_for_reklama: bool = False, theme: str = "") -> bool:
    """
    Multi-level advertisement detection.
    Migrated from old_postopus bin/utils/is_advertisement.py
    
    Levels:
    1. VK API marked_as_ads (handled separately in parser)
    2. Legal advertising markers (#реклама, #ad, erid:, etc.) - IMMEDIATE True
    3. Commercial patterns scoring (prices, discounts, CTA, contacts)
    4. Suspicious attachments (ads links)
    
    Args:
        text: Post text to analyze
        skip_for_reklama: If True, skip ad detection for reklama theme
        theme: Current theme (reklama, novost, etc.)
    
    Returns:
        True if post is advertisement
    """
    if not text:
        return False
    
    # Skip ad detection for reklama theme
    if skip_for_reklama and theme == "reklama":
        return False
    
    text_lower = text.lower()
    
    # Level 2: Legal advertising markers (IMMEDIATE True)
    legal_markers = [
        '#реклама', '#реклама',
        '#ad', '#ad',
        '#sponsored',
        '#партнёрство', '#партнерство',
        'erid:',
        'на правах рекламы',
    ]
    
    for marker in legal_markers:
        if marker.lower() in text_lower:
            return True
    
    # Level 3: Commercial patterns scoring
    commercial_patterns = {
        # Prices and discounts (weight 2)
        2: [
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
        # Calls to action (weight 1-2)
        2: [
            r'звоните[:\s]',
            r'пишите[:\s]',
            r'тел[.:]?\s*[\d+\-]',
            r'т[.:]?\s*[\d+\-]',
            r'моб[.:]?\s*[\d+\-]',
            r'whatsapp',
            r'telegram',
        ],
        1: [
            r'подробности',
            r'узнать больше',
            r'переходите',
            r'подписывайтесь',
        ],
    }
    
    score = 0
    for weight, patterns in commercial_patterns.items():
        for pattern in patterns:
            if re.search(pattern, text_lower):
                score += weight
    
    # Score >= 4 = advertisement
    if score >= 4:
        return True
    
    # Level 4: Suspicious attachments/links
    suspicious_links = [
        'vk.com/ads',
        'target.vk.com',
        'ads.vk.com',
    ]
    
    for link in suspicious_links:
        if link in text_lower:
            score += 1
    
    return score >= 4


def check_blacklist(text: str, blacklist: List[str]) -> Optional[str]:
    """
    Check if text contains any blacklisted words/phrases.
    
    Args:
        text: Text to check
        blacklist: List of blacklisted words/phrases
    
    Returns:
        Matched pattern or None
    """
    if not text or not blacklist:
        return None
    
    text_lower = text.lower()
    
    for pattern in blacklist:
        pattern_lower = pattern.lower()
        if pattern_lower in text_lower:
            return pattern
    
    return None


def truncate_text(text: str, max_length: int, suffix: str = "...") -> str:
    """
    Truncate text to max_length, adding suffix if truncated.
    
    Args:
        text: Input text
        max_length: Maximum length
        suffix: Suffix to add if truncated
    
    Returns:
        Truncated text
    """
    if not text or len(text) <= max_length:
        return text
    
    # Truncate and add suffix
    return text[:max_length - len(suffix)] + suffix


def extract_hashtags(text: str) -> List[str]:
    """Extract all hashtags from text."""
    if not text:
        return []
    
    return re.findall(r'#\w+', text)


def remove_hashtags(text: str) -> str:
    """Remove all hashtags from text."""
    if not text:
        return ""
    
    return re.sub(r'#\w+', '', text).strip()
