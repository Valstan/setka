"""
Image utilities migrated from old_postopus bin/rw/

Provides image downloading, size selection, and fingerprinting
for photo/video duplicate detection.
"""
import hashlib
import os
import requests
from typing import Optional, Tuple
from PIL import Image
import io


def get_link_image_select_size(
    image_url: str,
    min_width: int = 100,
    min_height: int = 100,
    max_width: int = 1000,
    max_height: int = 1000,
) -> Optional[bytes]:
    """
    Download image and select appropriate size within bounds.
    Migrated from old_postopus bin/rw/get_link_image_select_size.py
    
    Args:
        image_url: Image URL
        min_width: Minimum width
        min_height: Minimum height
        max_width: Maximum width
        max_height: Maximum height
    
    Returns:
        Image bytes or None
    """
    try:
        response = requests.get(image_url, timeout=10)
        response.raise_for_status()
        
        img = Image.open(io.BytesIO(response.content))
        width, height = img.size
        
        # Check if image is within bounds
        if width < min_width or height < min_height:
            return None
        
        # Resize if too large
        if width > max_width or height > max_height:
            # Maintain aspect ratio
            ratio = min(max_width / width, max_height / height)
            new_width = int(width * ratio)
            new_height = int(height * ratio)
            img = img.resize((new_width, new_height), Image.LANCZOS)
        
        # Convert back to bytes
        output = io.BytesIO()
        img.save(output, format=img.format or 'JPEG')
        return output.getvalue()
        
    except Exception as e:
        print(f"⚠️  Failed to process image {image_url}: {e}")
        return None


def download_image(image_url: str, save_path: str) -> bool:
    """
    Download image from URL to disk.
    Migrated from old_postopus bin/rw/get_image.py
    
    Args:
        image_url: Image URL
        save_path: Local path to save
    
    Returns:
        True if successful
    """
    try:
        response = requests.get(image_url, timeout=10)
        response.raise_for_status()
        
        os.makedirs(os.path.dirname(save_path) or '.', exist_ok=True)
        
        with open(save_path, 'wb') as f:
            f.write(response.content)
        
        return True
        
    except Exception as e:
        print(f"⚠️  Failed to download image {image_url}: {e}")
        return False


def image_to_histogram_md5(image_data: bytes) -> Optional[str]:
    """
    Compute histogram-based MD5 fingerprint of an image.
    Migrated from old_postopus bin/sort/sort_po_foto.py
    
    This is used for photo duplicate detection.
    
    Args:
        image_data: Image bytes
    
    Returns:
        MD5 hash string or None
    """
    try:
        img = Image.open(io.BytesIO(image_data))
        
        # Convert to RGB if necessary
        if img.mode != 'RGB':
            img = img.convert('RGB')
        
        # Resize to small size for fingerprint
        img = img.resize((32, 32), Image.LANCZOS)
        
        # Compute histogram
        histogram = img.histogram()
        
        # Normalize histogram
        total = sum(histogram)
        if total > 0:
            normalized = [h / total for h in histogram]
        else:
            normalized = histogram
        
        # Create MD5 hash
        histogram_str = ','.join(f"{v:.6f}" for v in normalized)
        md5_hash = hashlib.md5(histogram_str.encode()).hexdigest()
        
        return md5_hash
        
    except Exception as e:
        print(f"⚠️  Failed to compute image fingerprint: {e}")
        return None


def image_files_are_duplicate(file1: str, file2: str) -> bool:
    """
    Check if two image files are duplicates using histogram MD5.
    
    Args:
        file1: Path to first image
        file2: Path to second image
    
    Returns:
        True if images are duplicates
    """
    try:
        with open(file1, 'rb') as f1, open(file2, 'rb') as f2:
            hash1 = image_to_histogram_md5(f1.read())
            hash2 = image_to_histogram_md5(f2.read())
            
            return hash1 is not None and hash1 == hash2
            
    except Exception as e:
        print(f"⚠️  Failed to compare images: {e}")
        return False


def get_vk_attachment_photo(post_attachments: dict) -> Optional[dict]:
    """
    Extract photo attachment in VK API format for reposting.
    
    Args:
        post_attachments: VK post attachments dict
    
    Returns:
        Photo attachment dict or None
    """
    if not post_attachments:
        return None
    
    photos = post_attachments.get('photo', [])
    if photos:
        return photos[0]
    
    return None


def get_vk_attachment_video(post_attachments: dict) -> Optional[dict]:
    """
    Extract video attachment in VK API format for reposting.
    
    Args:
        post_attachments: VK post attachments dict
    
    Returns:
        Video attachment dict or None
    """
    if not post_attachments:
        return None
    
    videos = post_attachments.get('video', [])
    if videos:
        return videos[0]
    
    return None


def get_vk_attachment_audio(post_attachments: dict) -> Optional[dict]:
    """
    Extract audio attachment in VK API format for reposting.
    
    Args:
        post_attachments: VK post attachments dict
    
    Returns:
        Audio attachment dict or None
    """
    if not post_attachments:
        return None
    
    audios = post_attachments.get('audio', [])
    if audios:
        return audios[0]
    
    return None
