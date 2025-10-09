"""
VK Publisher - publishes content to VK groups
"""
import asyncio
import logging
from typing import Dict, Any, Optional
import vk_api
from vk_api.upload import VkUpload
import requests
from io import BytesIO
from PIL import Image

from database.models import Post
from modules.publisher.base_publisher import BasePublisher

logger = logging.getLogger(__name__)


class VKPublisher(BasePublisher):
    """Publisher for VK platform"""
    
    def __init__(self, access_token: str):
        """
        Initialize VK Publisher
        
        Args:
            access_token: VK API access token with posting permissions
        """
        super().__init__("vk")
        self.access_token = access_token
        self.session = None
        self.api = None
        self.upload = None
        self._initialize_session()
    
    def _initialize_session(self):
        """Initialize VK API session"""
        try:
            self.session = vk_api.VkApi(token=self.access_token)
            self.api = self.session.get_api()
            self.upload = VkUpload(self.session)
            self.logger.info("VK API session initialized successfully")
        except Exception as e:
            self.logger.error(f"Failed to initialize VK API session: {e}")
            raise
    
    async def check_connection(self) -> bool:
        """Check VK API connection"""
        try:
            # Simple API call to check connection
            result = self.api.users.get()
            return bool(result)
        except Exception as e:
            self.logger.error(f"VK connection check failed: {e}")
            return False
    
    async def publish_post(
        self,
        post: Post,
        target_id: str,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Publish post to VK group
        
        Args:
            post: Post object to publish
            target_id: VK group ID (negative integer as string)
            **kwargs: Additional parameters:
                - from_group: Post from group (default True)
                - signed: Add author signature (default False)
                - copyright: Link to original post
            
        Returns:
            Dictionary with publishing results
        """
        try:
            # Prepare text
            text = self.format_post_text(
                post,
                max_length=15000,  # VK limit
                add_source=kwargs.get('add_source', True)
            )
            
            # Extract media
            media = self.extract_media(post)
            
            # Upload photos if any
            attachments = []
            if media['photos']:
                photo_attachments = await self._upload_photos(
                    media['photos'],
                    target_id
                )
                attachments.extend(photo_attachments)
            
            # Add videos
            for video in media['videos']:
                video_str = f"video{video['owner_id']}_{video['video_id']}"
                attachments.append(video_str)
            
            # Prepare post parameters
            post_params = {
                'owner_id': int(target_id),
                'message': text,
                'from_group': kwargs.get('from_group', 1),
                'signed': kwargs.get('signed', 0),
            }
            
            # Add attachments if any
            if attachments:
                post_params['attachments'] = ','.join(attachments)
            
            # Add copyright if specified
            if kwargs.get('copyright'):
                post_params['copyright'] = kwargs['copyright']
            
            # Publish to wall
            result = self.api.wall.post(**post_params)
            
            self.log_success(post.id, target_id, result)
            
            return {
                'success': True,
                'platform': 'vk',
                'post_id': result.get('post_id'),
                'url': f"https://vk.com/wall{target_id}_{result.get('post_id')}"
            }
            
        except Exception as e:
            self.log_error(post.id, target_id, e)
            return {
                'success': False,
                'platform': 'vk',
                'error': str(e)
            }
    
    async def _upload_photos(
        self,
        photo_urls: list,
        group_id: str
    ) -> list:
        """
        Download and upload photos to VK
        
        Args:
            photo_urls: List of photo URLs
            group_id: VK group ID
            
        Returns:
            List of attachment strings
        """
        attachments = []
        
        for url in photo_urls[:10]:  # VK limit: 10 photos
            try:
                # Download photo
                response = requests.get(url, timeout=10)
                response.raise_for_status()
                
                # Convert to PIL Image
                image = Image.open(BytesIO(response.content))
                
                # Save to BytesIO
                img_io = BytesIO()
                image.save(img_io, format='JPEG', quality=85)
                img_io.seek(0)
                
                # Upload to VK
                photo = self.upload.photo_wall(
                    img_io,
                    group_id=abs(int(group_id))
                )
                
                if photo:
                    photo = photo[0]
                    attachment_str = f"photo{photo['owner_id']}_{photo['id']}"
                    attachments.append(attachment_str)
                    
            except Exception as e:
                self.logger.warning(f"Failed to upload photo {url}: {e}")
                continue
        
        return attachments
    
    async def schedule_post(
        self,
        post: Post,
        target_id: str,
        publish_date: int,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Schedule post for later publishing
        
        Args:
            post: Post object to publish
            target_id: VK group ID
            publish_date: Unix timestamp for publishing
            **kwargs: Additional parameters
            
        Returns:
            Dictionary with scheduling results
        """
        try:
            text = self.format_post_text(post, max_length=15000)
            media = self.extract_media(post)
            
            # Upload photos
            attachments = []
            if media['photos']:
                photo_attachments = await self._upload_photos(
                    media['photos'],
                    target_id
                )
                attachments.extend(photo_attachments)
            
            # Schedule post
            result = self.api.wall.post(
                owner_id=int(target_id),
                message=text,
                attachments=','.join(attachments) if attachments else None,
                from_group=1,
                publish_date=publish_date
            )
            
            return {
                'success': True,
                'platform': 'vk',
                'post_id': result.get('post_id'),
                'scheduled': True,
                'publish_date': publish_date
            }
            
        except Exception as e:
            self.log_error(post.id, target_id, e)
            return {
                'success': False,
                'platform': 'vk',
                'error': str(e)
            }

