"""
Fingerprint generation functions for deduplication
Inspired by Postopus project's proven patterns
"""
import re
import hashlib
from typing import List, Dict, Any, Optional


def create_lip_fingerprint(owner_id: int, post_id: int) -> str:
    """
    Create structural fingerprint (lip) from VK post IDs
    
    This is the fastest and most reliable way to detect exact duplicates.
    Pattern from Postopus: worked flawlessly for 3+ years.
    
    Args:
        owner_id: VK owner_id (usually negative for groups)
        post_id: VK post_id
        
    Returns:
        Fingerprint string like "-12345678_9012"
        
    Example:
        >>> create_lip_fingerprint(-170319760, 3512)
        '-170319760_3512'
    """
    return f"{owner_id}_{post_id}"


def create_media_fingerprint(attachments: List[Dict[str, Any]]) -> List[str]:
    """
    Create media fingerprint from VK attachments
    
    Extracts unique IDs of photos and videos for duplicate detection.
    
    Args:
        attachments: List of VK attachment objects
        
    Returns:
        List of media IDs (photo IDs, video IDs)
        
    Example:
        >>> attachments = [
        ...     {'type': 'photo', 'photo': {'id': 457239017}},
        ...     {'type': 'photo', 'photo': {'id': 457239018}}
        ... ]
        >>> create_media_fingerprint(attachments)
        ['photo_457239017', 'photo_457239018']
    """
    if not attachments:
        return []
    
    media_ids = []
    
    for attachment in attachments:
        att_type = attachment.get('type')
        
        if att_type == 'photo':
            photo_id = attachment.get('photo', {}).get('id')
            if photo_id:
                media_ids.append(f"photo_{photo_id}")
        
        elif att_type == 'video':
            video = attachment.get('video', {})
            owner_id = video.get('owner_id')
            video_id = video.get('id')
            if owner_id and video_id:
                media_ids.append(f"video_{owner_id}_{video_id}")
        
        elif att_type == 'doc' and attachment.get('doc', {}).get('type') == 3:  # GIF
            doc_id = attachment.get('doc', {}).get('id')
            if doc_id:
                media_ids.append(f"doc_{doc_id}")
    
    return media_ids


def text_to_rafinad(text: str) -> str:
    """
    Convert text to "rafinad" (refined sugar) - cleaned essence
    
    This is the core pattern from Postopus for text deduplication.
    Removes all noise (spaces, punctuation, emoji) leaving only the content.
    
    Args:
        text: Original text
        
    Returns:
        Cleaned text in lowercase with only alphanumeric characters
        
    Example:
        >>> text_to_rafinad("🔥 В Малмыже пройдет концерт! 25 октября в ДК.")
        'вмалмыжепройдетконцерт25октябрявдк'
    """
    if not text:
        return ""
    
    # Convert to lowercase
    text = text.lower()
    
    # Remove all non-alphanumeric characters (keep only letters and digits)
    # This includes spaces, punctuation, emoji, etc.
    text = re.sub(r'[^a-zа-яёa-z0-9]', '', text)
    
    return text


def create_text_fingerprint(text: str) -> str:
    """
    Create fingerprint from full "rafinad" text
    
    Uses SHA256 hash for compact storage and fast comparison.
    
    Args:
        text: Original text
        
    Returns:
        SHA256 hash (first 32 characters)
        
    Example:
        >>> create_text_fingerprint("В Малмыже концерт")
        'a1b2c3d4e5f6...'
    """
    if not text:
        return ""
    
    rafinad = text_to_rafinad(text)
    
    if not rafinad:
        return ""
    
    # Create hash
    hash_obj = hashlib.sha256(rafinad.encode('utf-8'))
    return hash_obj.hexdigest()[:32]


def create_text_core_fingerprint(text: str) -> str:
    """
    Create fingerprint from text "core" (20-70% of content)
    
    This is the key innovation from Postopus:
    - Beginning and end of posts often differ (greetings, signatures)
    - But the CORE content should be unique
    - Taking 20-70% captures the essence while ignoring variations
    
    Args:
        text: Original text
        
    Returns:
        SHA256 hash of the core (first 32 characters)
        
    Example:
        >>> text = "Здравствуйте! В Малмыже 25 октября концерт. Приходите!"
        >>> # Core will be from 20% to 70% of rafinad
        >>> create_text_core_fingerprint(text)
        'x1y2z3...'
    """
    if not text:
        return ""
    
    rafinad = text_to_rafinad(text)
    
    if not rafinad or len(rafinad) < 20:
        # Too short - use full text
        return create_text_fingerprint(text)
    
    # Extract core (20% to 70% of length)
    start = len(rafinad) // 5  # 20%
    end = start + len(rafinad) // 2  # +50% = 70% total
    
    core = rafinad[start:end]
    
    # Create hash
    hash_obj = hashlib.sha256(core.encode('utf-8'))
    return hash_obj.hexdigest()[:32]


def extract_text_features(text: str) -> Dict[str, Any]:
    """
    Extract various features from text for analysis
    
    Args:
        text: Original text
        
    Returns:
        Dictionary with features:
        - length: Character count
        - rafinad_length: Cleaned text length
        - has_emoji: Contains emoji
        - has_urls: Contains URLs
        - has_hashtags: Contains hashtags
    """
    if not text:
        return {
            'length': 0,
            'rafinad_length': 0,
            'has_emoji': False,
            'has_urls': False,
            'has_hashtags': False
        }
    
    rafinad = text_to_rafinad(text)
    
    return {
        'length': len(text),
        'rafinad_length': len(rafinad),
        'has_emoji': bool(re.search(r'[\U0001F600-\U0001F64F]', text)),
        'has_urls': bool(re.search(r'https?://', text)),
        'has_hashtags': bool(re.search(r'#\w+', text))
    }

