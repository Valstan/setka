"""
VK API Client for SETKA project
Handles all interactions with VK API
"""
import vk_api
import asyncio
from typing import List, Dict, Any, Optional
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class VKClient:
    """VK API Client with token rotation and rate limiting"""
    
    def __init__(self, token: str):
        """Initialize VK client with token"""
        self.token = token
        self.session = None
        self.vk = None
        self._init_session()
    
    def _init_session(self):
        """Initialize VK session"""
        try:
            self.session = vk_api.VkApi(token=self.token)
            self.vk = self.session.get_api()
            logger.info("VK session initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize VK session: {e}")
            raise
    
    def get_wall_posts(
        self, 
        owner_id: int, 
        count: int = 10,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """
        Get posts from VK community wall
        
        Args:
            owner_id: VK group ID (negative for communities)
            count: Number of posts to fetch (max 100)
            offset: Offset for pagination
            
        Returns:
            List of posts
        """
        try:
            response = self.vk.wall.get(
                owner_id=owner_id,
                count=min(count, 100),
                offset=offset
            )
            
            posts = response.get('items', [])
            logger.info(f"Fetched {len(posts)} posts from {owner_id}")
            return posts
            
        except vk_api.exceptions.ApiError as e:
            logger.error(f"VK API error for {owner_id}: {e}")
            return []
        except Exception as e:
            logger.error(f"Unexpected error fetching posts from {owner_id}: {e}")
            return []
    
    def get_post_by_id(self, owner_id: int, post_id: int) -> Optional[Dict[str, Any]]:
        """
        Get specific post by ID
        
        Args:
            owner_id: VK group ID
            post_id: Post ID
            
        Returns:
            Post data or None
        """
        try:
            posts_str = f"{owner_id}_{post_id}"
            response = self.vk.wall.getById(posts=[posts_str])
            
            if response:
                return response[0]
            return None
            
        except vk_api.exceptions.ApiError as e:
            logger.error(f"VK API error getting post {owner_id}_{post_id}: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error getting post {owner_id}_{post_id}: {e}")
            return None
    
    def get_group_info(self, group_id: int) -> Optional[Dict[str, Any]]:
        """
        Get information about VK group
        
        Args:
            group_id: VK group ID (positive or negative)
            
        Returns:
            Group info or None
        """
        try:
            # Convert to positive ID if needed
            group_id = abs(group_id)
            
            response = self.vk.groups.getById(group_id=group_id)
            
            if response:
                return response[0]
            return None
            
        except vk_api.exceptions.ApiError as e:
            logger.error(f"VK API error getting group info {group_id}: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error getting group info {group_id}: {e}")
            return None
    
    def parse_attachments(self, post: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Parse post attachments (photos, videos, links, etc.)
        
        Args:
            post: VK post data
            
        Returns:
            List of parsed attachments
        """
        attachments = []
        
        if 'attachments' not in post:
            return attachments
        
        for att in post['attachments']:
            att_type = att.get('type')
            
            if att_type == 'photo':
                photo = att['photo']
                # Get largest photo size
                sizes = photo.get('sizes', [])
                if sizes:
                    largest = max(sizes, key=lambda x: x.get('width', 0) * x.get('height', 0))
                    attachments.append({
                        'type': 'photo',
                        'url': largest.get('url'),
                        'width': largest.get('width'),
                        'height': largest.get('height')
                    })
            
            elif att_type == 'video':
                video = att['video']
                attachments.append({
                    'type': 'video',
                    'title': video.get('title'),
                    'duration': video.get('duration'),
                    'views': video.get('views', 0)
                })
            
            elif att_type == 'link':
                link = att['link']
                attachments.append({
                    'type': 'link',
                    'url': link.get('url'),
                    'title': link.get('title')
                })
            
            elif att_type == 'doc':
                doc = att['doc']
                attachments.append({
                    'type': 'document',
                    'title': doc.get('title'),
                    'url': doc.get('url')
                })
        
        return attachments
    
    def extract_post_stats(self, post: Dict[str, Any]) -> Dict[str, int]:
        """
        Extract statistics from post
        
        Args:
            post: VK post data
            
        Returns:
            Dictionary with stats
        """
        return {
            'views': post.get('views', {}).get('count', 0),
            'likes': post.get('likes', {}).get('count', 0),
            'reposts': post.get('reposts', {}).get('count', 0),
            'comments': post.get('comments', {}).get('count', 0)
        }
    
    async def check_token_validity(self) -> bool:
        """
        Check if token is still valid
        
        Returns:
            True if token is valid, False otherwise
        """
        try:
            # Try to fetch user info
            self.vk.users.get()
            return True
        except Exception as e:
            logger.error(f"Token validation failed: {e}")
            return False


class VKTokenRotator:
    """Rotates between multiple VK tokens to avoid rate limits"""
    
    def __init__(self, tokens: List[str]):
        """
        Initialize token rotator
        
        Args:
            tokens: List of VK API tokens
        """
        self.tokens = tokens
        self.current_index = 0
        self.clients = [VKClient(token) for token in tokens if token]
    
    def get_client(self) -> Optional[VKClient]:
        """
        Get next available VK client
        
        Returns:
            VKClient instance or None if no clients available
        """
        if not self.clients:
            logger.error("No VK clients available")
            return None
        
        client = self.clients[self.current_index]
        self.current_index = (self.current_index + 1) % len(self.clients)
        
        return client
    
    async def check_all_tokens(self) -> int:
        """
        Check validity of all tokens
        
        Returns:
            Number of valid tokens
        """
        valid_count = 0
        
        for client in self.clients:
            if await client.check_token_validity():
                valid_count += 1
        
        return valid_count

