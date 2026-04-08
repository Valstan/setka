"""
Post utilities migrated from old_postopus bin/utils/

Provides post ID generation, URL building, popularity scoring,
and other common post operations.
"""
import math
from typing import Dict, Any, Optional


def lip_of_post(owner_id: int, post_id: int) -> str:
    """
    Generate unique post ID (lip).
    Migrated from old_postopus bin/utils/lip_of_post.py
    
    Format: "{abs(owner_id)}_{id}"
    Used for deduplication tracking.
    
    Args:
        owner_id: VK owner ID (usually negative for groups)
        post_id: VK post ID
    
    Returns:
        Unique lip string
    """
    return f"{abs(owner_id)}_{post_id}"


def parse_lip(lip: str) -> tuple:
    """
    Parse lip string back into owner_id and post_id.
    
    Args:
        lip: "{abs(owner_id)}_{id}"
    
    Returns:
        (owner_id, post_id) tuple
    """
    parts = lip.split('_')
    if len(parts) != 2:
        raise ValueError(f"Invalid lip format: {lip}")
    
    return int(parts[0]), int(parts[1])


def url_of_post(owner_id: int, post_id: int) -> str:
    """
    Build VK post URL.
    Migrated from old_postopus bin/utils/url_of_post.py
    
    Args:
        owner_id: VK owner ID
        post_id: VK post ID
    
    Returns:
        Full VK post URL
    """
    return f"https://vk.com/wall{owner_id}_{post_id}"


def post_popularity(views: int, likes: int, comments: int, reposts: int) -> float:
    """
    Calculate post popularity score.
    Migrated from old_postopus bin/utils/post_popularity.py
    
    Formula: (likes + comments*2 + reposts*3) / sqrt(views+1)
    This balances engagement with reach.
    
    Args:
        views: Number of views
        likes: Number of likes
        comments: Number of comments
        reposts: Number of reposts
    
    Returns:
        Popularity score (higher = more popular)
    """
    if views == 0 and likes == 0 and comments == 0 and reposts == 0:
        return 0.0
    
    engagement = likes + (comments * 2) + (reposts * 3)
    view_factor = math.sqrt(views + 1)
    
    return engagement / view_factor


def post_popularity_v2(views: int, likes: int, comments: int, reposts: int) -> float:
    """
    Improved popularity score with better weighting.
    Modern version for SETKA.
    
    Args:
        views: Number of views
        likes: Number of likes
        comments: Number of comments
        reposts: Number of reposts
    
    Returns:
        Popularity score (0-100 scale)
    """
    if views == 0 and likes == 0 and comments == 0 and reposts == 0:
        return 0.0
    
    # Weighted engagement
    engagement = (likes * 1.0) + (comments * 2.0) + (reposts * 3.0)
    
    # Normalize by views (logarithmic scale to avoid extreme values)
    view_factor = math.log10(views + 1) + 1
    
    score = (engagement / view_factor) * 10
    
    # Cap at 100
    return min(score, 100.0)


def clear_copy_history(post_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Unwrap repost copy_history to get original post.
    Migrated from old_postopus bin/utils/clear_copy_history.py
    
    When a post is a repost, VK stores the original in copy_history.
    This function extracts the original post data.
    
    Args:
        post_data: Post data from VK API
    
    Returns:
        Original post data (unwrapped)
    """
    if not post_data:
        return post_data
    
    # Check for copy_history (repost)
    copy_history = post_data.get('copy_history', [])
    
    if copy_history and len(copy_history) > 0:
        # Get the deepest (original) post
        original = copy_history[0]
        
        # Merge with current post metadata
        merged = {
            **original,
            # Keep some metadata from the repost
            'repost_from_id': post_data.get('id'),
            'repost_from_owner_id': post_data.get('owner_id'),
            'is_repost': True,
        }
        
        return merged
    
    # Not a repost
    post_data['is_repost'] = False
    return post_data


def format_post_stats(views: int, likes: int, comments: int, reposts: int) -> str:
    """
    Format post statistics for display.
    
    Args:
        views, likes, comments, reposts
    
    Returns:
        Formatted string
    """
    parts = []
    
    if views > 0:
        parts.append(f"👁 {format_number(views)}")
    if likes > 0:
        parts.append(f"❤️ {format_number(likes)}")
    if comments > 0:
        parts.append(f"💬 {format_number(comments)}")
    if reposts > 0:
        parts.append(f"🔄 {format_number(reposts)}")
    
    return " | ".join(parts) if parts else "Нет статистики"


def format_number(num: int) -> str:
    """Format number for display (1K, 1M, etc.)"""
    if num >= 1_000_000:
        return f"{num / 1_000_000:.1f}M"
    elif num >= 1_000:
        return f"{num / 1_000:.1f}K"
    else:
        return str(num)


def extract_source_attribution(post_data: Dict[str, Any], group_name: str = "") -> str:
    """
    Extract source attribution for digest.
    Format: @{url} (group_name)
    
    Args:
        post_data: VK post data
        group_name: Group display name
    
    Returns:
        Attribution string
    """
    owner_id = post_data.get('owner_id', post_data.get('from_id', 0))
    post_id = post_data.get('id', 0)
    
    vk_url = f"https://vk.com/wall{owner_id}_{post_id}"
    
    if group_name:
        return f"@{vk_url} ({group_name})"
    else:
        return f"@{vk_url}"
