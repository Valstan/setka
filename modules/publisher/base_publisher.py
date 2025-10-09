"""
Base publisher class for all publishing platforms
"""
import logging
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from database.models import Post

logger = logging.getLogger(__name__)


class BasePublisher(ABC):
    """Base class for content publishers"""
    
    def __init__(self, platform_name: str):
        """
        Initialize publisher
        
        Args:
            platform_name: Name of the platform (vk, telegram, wordpress)
        """
        self.platform_name = platform_name
        self.logger = logging.getLogger(f"publisher.{platform_name}")
    
    @abstractmethod
    async def publish_post(
        self,
        post: Post,
        target_id: str,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Publish post to platform
        
        Args:
            post: Post object to publish
            target_id: Target group/channel ID
            **kwargs: Additional platform-specific parameters
            
        Returns:
            Dictionary with publishing results
        """
        pass
    
    @abstractmethod
    async def check_connection(self) -> bool:
        """
        Check if connection to platform is valid
        
        Returns:
            True if connection is OK
        """
        pass
    
    def format_post_text(
        self,
        post: Post,
        max_length: Optional[int] = None,
        add_source: bool = True
    ) -> str:
        """
        Format post text for publishing
        
        Args:
            post: Post object
            max_length: Maximum text length
            add_source: Add source attribution
            
        Returns:
            Formatted text
        """
        text = post.text or ""
        
        # Add source if requested
        if add_source and post.community:
            source_link = f"https://vk.com/wall{post.vk_owner_id}_{post.vk_post_id}"
            source_text = f"\n\n📰 Источник: {post.community.name}\n🔗 {source_link}"
            text += source_text
        
        # Truncate if needed
        if max_length and len(text) > max_length:
            text = text[:max_length - 3] + "..."
        
        return text
    
    def extract_media(self, post: Post) -> Dict[str, list]:
        """
        Extract media from post attachments
        
        Args:
            post: Post object
            
        Returns:
            Dictionary with photos, videos, documents
        """
        result = {
            'photos': [],
            'videos': [],
            'documents': []
        }
        
        if not post.attachments:
            return result
        
        for attachment in post.attachments:
            att_type = attachment.get('type')
            
            if att_type == 'photo':
                # Get largest photo size
                sizes = attachment.get('photo', {}).get('sizes', [])
                if sizes:
                    largest = max(sizes, key=lambda x: x.get('width', 0) * x.get('height', 0))
                    result['photos'].append(largest.get('url'))
            
            elif att_type == 'video':
                video = attachment.get('video', {})
                result['videos'].append({
                    'owner_id': video.get('owner_id'),
                    'video_id': video.get('id')
                })
            
            elif att_type == 'doc':
                doc = attachment.get('doc', {})
                result['documents'].append({
                    'url': doc.get('url'),
                    'title': doc.get('title')
                })
        
        return result
    
    def log_success(self, post_id: int, target: str, result: Any):
        """Log successful publishing"""
        self.logger.info(
            f"✅ Post {post_id} published to {self.platform_name}:{target} - {result}"
        )
    
    def log_error(self, post_id: int, target: str, error: Exception):
        """Log publishing error"""
        self.logger.error(
            f"❌ Failed to publish post {post_id} to {self.platform_name}:{target}: {error}"
        )

