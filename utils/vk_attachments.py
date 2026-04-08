"""
VK attachment utilities

Extracts and formats VK post attachments (photos, videos, audio)
for use in digest building and publishing.
"""
from typing import Dict, List, Any, Optional, Tuple


def extract_vk_attachments(post_data: Dict[str, Any]) -> Dict[str, List[Dict]]:
    """
    Extract all attachments from a VK post.
    
    Args:
        post_data: VK API post data
    
    Returns:
        Dict with attachment types: {photo: [...], video: [...], audio: [...], link: [...]}
    """
    attachments = {
        'photo': [],
        'video': [],
        'audio': [],
        'link': [],
        'doc': [],
    }
    
    if not post_data:
        return attachments
    
    # Direct attachments
    for attach_type in attachments.keys():
        if attach_type in post_data:
            attachments[attach_type] = post_data[attach_type]
    
    # Attachments array (newer API format)
    raw_attachments = post_data.get('attachments', [])
    
    for attachment in raw_attachments:
        attach_type = attachment.get('type')
        if attach_type and attach_type in attachments:
            attachments[attach_type].append(attachment.get(attach_type, {}))
    
    return attachments


def format_vk_attachment_string(attachment_type: str, attachment_data: Dict) -> Optional[str]:
    """
    Format attachment as VK API attachment string for wall.post.
    
    Format: "{owner_id}_{media_id}" or "{type}{owner_id}_{media_id}"
    
    Args:
        attachment_type: photo, video, audio, doc
        attachment_data: Attachment data dict
    
    Returns:
        Formatted string or None
    """
    if not attachment_data:
        return None
    
    owner_id = attachment_data.get('owner_id')
    media_id = attachment_data.get('id')
    
    if owner_id is None or media_id is None:
        return None
    
    if attachment_type == 'photo':
        return f"photo{owner_id}_{media_id}"
    elif attachment_type == 'video':
        return f"video{owner_id}_{media_id}"
    elif attachment_type == 'audio':
        return f"audio{owner_id}_{media_id}"
    elif attachment_type == 'doc':
        return f"doc{owner_id}_{media_id}"
    
    return None


def build_attachments_list(attachments: Dict[str, List[Dict]], max_items: int = 10) -> List[str]:
    """
    Build list of VK attachment strings for wall.post.
    
    VK wall.post accepts up to 10 media attachments.
    
    Args:
        attachments: Dict from extract_vk_attachments()
        max_items: Maximum number of attachments (VK limit: 10)
    
    Returns:
        List of attachment strings
    """
    result = []
    
    # Photos first (most important)
    for photo in attachments.get('photo', []):
        if len(result) >= max_items:
            break
        attachment_str = format_vk_attachment_string('photo', photo)
        if attachment_str:
            result.append(attachment_str)
    
    # Then videos
    for video in attachments.get('video', []):
        if len(result) >= max_items:
            break
        attachment_str = format_vk_attachment_string('video', video)
        if attachment_str:
            result.append(attachment_str)
    
    # Then audio
    for audio in attachments.get('audio', []):
        if len(result) >= max_items:
            break
        attachment_str = format_vk_attachment_string('audio', audio)
        if attachment_str:
            result.append(attachment_str)
    
    # Then docs
    for doc in attachments.get('doc', []):
        if len(result) >= max_items:
            break
        attachment_str = format_vk_attachment_string('doc', doc)
        if attachment_str:
            result.append(attachment_str)
    
    return result


def get_photo_urls(attachments: Dict[str, List[Dict]], max_photos: int = 10) -> List[str]:
    """
    Extract best quality photo URLs from attachments.
    
    Args:
        attachments: Dict from extract_vk_attachments()
        max_photos: Maximum number of photos to extract
    
    Returns:
        List of photo URLs (best quality for each)
    """
    urls = []
    
    for photo in attachments.get('photo', []):
        if len(urls) >= max_photos:
            break
        
        # VK provides multiple sizes, get the largest
        sizes = photo.get('sizes', [])
        if sizes:
            # Sort by width, get largest
            sizes_sorted = sorted(sizes, key=lambda s: s.get('width', 0), reverse=True)
            best_size = sizes_sorted[0]
            url = best_size.get('url')
            if url:
                urls.append(url)
        elif 'url' in photo:
            urls.append(photo['url'])
    
    return urls


def get_video_info(attachments: Dict[str, List[Dict]]) -> List[Dict[str, Any]]:
    """
    Extract video information from attachments.
    
    Args:
        attachments: Dict from extract_vk_attachments()
    
    Returns:
        List of video info dicts with title, duration, player URL
    """
    videos = []
    
    for video in attachments.get('video', []):
        video_info = {
            'owner_id': video.get('owner_id'),
            'id': video.get('id'),
            'title': video.get('title', ''),
            'duration': video.get('duration', 0),
            'player': video.get('player', ''),
            'image': video.get('image', [{}])[-1].get('url') if video.get('image') else None,
        }
        videos.append(video_info)
    
    return videos


def count_attachments(attachments: Dict[str, List[Dict]]) -> int:
    """Count total number of attachments."""
    return sum(len(items) for items in attachments.values())


def has_attachments(attachments: Dict[str, List[Dict]]) -> bool:
    """Check if post has any media attachments."""
    return count_attachments(attachments) > 0


def has_video_attachments(attachments: Dict[str, List[Dict]]) -> bool:
    """Check if post has video attachments."""
    return len(attachments.get('video', [])) > 0


def has_photo_attachments(attachments: Dict[str, List[Dict]]) -> bool:
    """Check if post has photo attachments."""
    return len(attachments.get('photo', [])) > 0


def has_audio_attachments(attachments: Dict[str, List[Dict]]) -> bool:
    """Check if post has audio attachments."""
    return len(attachments.get('audio', [])) > 0
