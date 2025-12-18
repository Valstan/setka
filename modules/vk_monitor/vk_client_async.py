"""
Async VK API Client with Connection Pooling
Значительно быстрее синхронной версии за счёт:
- aiohttp session pooling
- Async requests
- Connection reuse
- Retry logic
"""
import aiohttp
import asyncio
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type
)

from core.exceptions import (
    VKAPIException,
    VKRateLimitException,
    VKAccessDeniedException,
    VKTokenInvalidException,
    handle_vk_error
)
from monitoring.metrics import (
    track_vk_request,
    vk_api_errors_total,
    vk_api_rate_limit_hits
)

logger = logging.getLogger(__name__)


class VKClientAsync:
    """
    Async VK API Client with connection pooling
    
    Performance improvements:
    - Connection pooling (10 connections)
    - Async requests
    - Automatic retries
    - Rate limiting built-in
    """
    
    VK_API_VERSION = "5.131"
    VK_API_URL = "https://api.vk.com/method/"
    
    def __init__(
        self,
        token: str,
        connector_limit: int = 10,
        connector_limit_per_host: int = 5,
        timeout: int = 30
    ):
        """
        Initialize async VK client
        
        Args:
            token: VK API access token
            connector_limit: Max total connections
            connector_limit_per_host: Max connections per host
            timeout: Request timeout in seconds
        """
        self.token = token
        self.connector_limit = connector_limit
        self.connector_limit_per_host = connector_limit_per_host
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        
        # Session will be created on first use
        self._session: Optional[aiohttp.ClientSession] = None
        self._connector: Optional[aiohttp.TCPConnector] = None
    
    async def __aenter__(self):
        """Async context manager entry"""
        await self._ensure_session()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        await self.close()
    
    async def _ensure_session(self):
        """Ensure session exists, create if needed"""
        if self._session is None or self._session.closed:
            self._connector = aiohttp.TCPConnector(
                limit=self.connector_limit,
                limit_per_host=self.connector_limit_per_host,
                ttl_dns_cache=300,  # DNS cache for 5 minutes
                force_close=False,  # Reuse connections
                enable_cleanup_closed=True
            )
            
            self._session = aiohttp.ClientSession(
                connector=self._connector,
                timeout=self.timeout,
                headers={
                    "User-Agent": "SETKA/1.0 (VK News Aggregator)"
                }
            )
            
            logger.info(f"VK Async session created (pool: {self.connector_limit} connections)")
    
    async def close(self):
        """Close session and connector"""
        if self._session and not self._session.closed:
            await self._session.close()
            logger.info("VK Async session closed")
        
        if self._connector and not self._connector.closed:
            await self._connector.close()
            logger.info("VK Async connector closed")
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((aiohttp.ClientError, asyncio.TimeoutError))
    )
    async def _make_request(
        self,
        method: str,
        params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Make VK API request with automatic retries
        
        Args:
            method: VK API method name (e.g., 'wall.get')
            params: Request parameters
            
        Returns:
            API response data
            
        Raises:
            VKAPIException: On VK API errors
        """
        await self._ensure_session()
        
        # Add token and version
        params['access_token'] = self.token
        params['v'] = self.VK_API_VERSION
        
        url = f"{self.VK_API_URL}{method}"
        
        try:
            async with self._session.get(url, params=params) as response:
                data = await response.json()
                
                # Check for VK API errors
                if 'error' in data:
                    error = data['error']
                    error_code = error.get('error_code')
                    error_msg = error.get('error_msg', 'Unknown error')
                    
                    logger.error(f"VK API error [{error_code}]: {error_msg}")
                    
                    # Track error in metrics
                    vk_api_errors_total.labels(error_code=str(error_code)).inc()
                    
                    # Handle rate limit (code 6)
                    if error_code == 6:
                        vk_api_rate_limit_hits.inc()
                        logger.warning("Rate limit hit, waiting...")
                        await asyncio.sleep(1)
                    
                    # Raise appropriate exception
                    handle_vk_error(error_code, error_msg, method)
                
                return data.get('response', {})
        
        except aiohttp.ClientError as e:
            logger.error(f"HTTP error for {method}: {e}")
            raise
        except asyncio.TimeoutError:
            logger.error(f"Timeout for {method}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error for {method}: {e}")
            raise VKAPIException(str(e))
    
    async def get_wall_posts(
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
            response = await self._make_request('wall.get', {
                'owner_id': owner_id,
                'count': min(count, 100),
                'offset': offset
            })
            
            posts = response.get('items', [])
            logger.debug(f"Fetched {len(posts)} posts from {owner_id}")
            return posts
        
        except VKAPIException as e:
            logger.error(f"Failed to fetch posts from {owner_id}: {e.message}")
            return []
        except Exception as e:
            logger.error(f"Unexpected error fetching posts from {owner_id}: {e}")
            return []
    
    async def get_post_by_id(
        self,
        owner_id: int,
        post_id: int
    ) -> Optional[Dict[str, Any]]:
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
            response = await self._make_request('wall.getById', {
                'posts': posts_str
            })
            
            if response and isinstance(response, list) and len(response) > 0:
                return response[0]
            return None
        
        except VKAPIException as e:
            logger.error(f"Failed to get post {owner_id}_{post_id}: {e.message}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error getting post {owner_id}_{post_id}: {e}")
            return None
    
    async def get_group_info(self, group_id: int) -> Optional[Dict[str, Any]]:
        """
        Get information about VK group
        
        Args:
            group_id: VK group ID (positive or negative)
            
        Returns:
            Group info or None
        """
        try:
            # Convert to positive ID
            group_id = abs(group_id)
            
            response = await self._make_request('groups.getById', {
                'group_id': group_id,
                'fields': 'members_count,description,verified'
            })
            
            if response and isinstance(response, list) and len(response) > 0:
                return response[0]
            return None
        
        except VKAPIException as e:
            logger.error(f"Failed to get group info {group_id}: {e.message}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error getting group info {group_id}: {e}")
            return None
    
    async def get_multiple_groups_info(
        self,
        group_ids: List[int]
    ) -> List[Dict[str, Any]]:
        """
        Get information about multiple groups in one request
        
        Args:
            group_ids: List of VK group IDs
            
        Returns:
            List of group info dicts
        """
        try:
            # Convert to positive IDs and join
            positive_ids = [abs(gid) for gid in group_ids]
            ids_str = ','.join(map(str, positive_ids))
            
            response = await self._make_request('groups.getById', {
                'group_ids': ids_str,
                'fields': 'members_count,description,verified'
            })
            
            return response if isinstance(response, list) else []
        
        except VKAPIException as e:
            logger.error(f"Failed to get groups info: {e.message}")
            return []
        except Exception as e:
            logger.error(f"Unexpected error getting groups info: {e}")
            return []
    
    @staticmethod
    def parse_attachments(post: Dict[str, Any]) -> List[Dict[str, Any]]:
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
                sizes = photo.get('sizes', [])
                if sizes:
                    # Get largest photo
                    largest = max(sizes, key=lambda x: x.get('width', 0) * x.get('height', 0))
                    attachments.append({
                        'type': 'photo',
                        'url': largest.get('url'),
                        'width': largest.get('width'),
                        'height': largest.get('height'),
                        'photo_id': f"photo{photo.get('owner_id')}_{photo.get('id')}"
                    })
            
            elif att_type == 'video':
                video = att['video']
                attachments.append({
                    'type': 'video',
                    'title': video.get('title'),
                    'duration': video.get('duration'),
                    'views': video.get('views', 0),
                    'video_id': f"video{video.get('owner_id')}_{video.get('id')}"
                })
            
            elif att_type == 'link':
                link = att['link']
                attachments.append({
                    'type': 'link',
                    'url': link.get('url'),
                    'title': link.get('title'),
                    'description': link.get('description')
                })
            
            elif att_type == 'doc':
                doc = att['doc']
                attachments.append({
                    'type': 'document',
                    'title': doc.get('title'),
                    'url': doc.get('url'),
                    'size': doc.get('size', 0)
                })
        
        return attachments
    
    @staticmethod
    def extract_post_stats(post: Dict[str, Any]) -> Dict[str, int]:
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
            True if token is valid
        """
        try:
            await self._make_request('users.get', {})
            return True
        except Exception as e:
            logger.error(f"Token validation failed: {e}")
            return False


class VKTokenRotatorAsync:
    """Async version of token rotator with connection pooling"""
    
    def __init__(self, tokens: List[str]):
        """
        Initialize async token rotator
        
        Args:
            tokens: List of VK API tokens
        """
        self.tokens = [t for t in tokens if t]
        self.current_index = 0
        self._clients: Dict[str, VKClientAsync] = {}
        
        logger.info(f"VK Token Rotator initialized with {len(self.tokens)} tokens")
    
    async def get_client(self) -> Optional[VKClientAsync]:
        """
        Get next available VK client
        
        Returns:
            VKClientAsync instance or None
        """
        if not self.tokens:
            logger.error("No VK tokens available")
            return None
        
        token = self.tokens[self.current_index]
        
        # Create or reuse client
        if token not in self._clients:
            self._clients[token] = VKClientAsync(token)
        
        client = self._clients[token]
        self.current_index = (self.current_index + 1) % len(self.tokens)
        
        return client
    
    async def check_all_tokens(self) -> int:
        """
        Check validity of all tokens
        
        Returns:
            Number of valid tokens
        """
        valid_count = 0
        
        for token in self.tokens:
            client = VKClientAsync(token)
            if await client.check_token_validity():
                valid_count += 1
            await client.close()
        
        logger.info(f"Token check: {valid_count}/{len(self.tokens)} valid")
        return valid_count
    
    async def close_all(self):
        """Close all client sessions"""
        for client in self._clients.values():
            await client.close()
        
        self._clients.clear()
        logger.info("All VK clients closed")


if __name__ == "__main__":
    # Test
    import sys
    
    async def test():
        if len(sys.argv) < 2:
            print("Usage: python vk_client_async.py <VK_TOKEN>")
            return
        
        token = sys.argv[1]
        
        print("Testing VKClientAsync...")
        
        async with VKClientAsync(token) as client:
            # Test group info
            print("\n1. Getting group info...")
            group = await client.get_group_info(-221432488)
            if group:
                print(f"   Group: {group.get('name')}")
                print(f"   Members: {group.get('members_count')}")
            
            # Test wall posts
            print("\n2. Getting wall posts...")
            posts = await client.get_wall_posts(-221432488, count=5)
            print(f"   Fetched {len(posts)} posts")
            
            if posts:
                post = posts[0]
                print(f"   Latest post text: {post.get('text', '')[:100]}...")
                
                # Test stats
                stats = client.extract_post_stats(post)
                print(f"   Stats: {stats}")
                
                # Test attachments
                attachments = client.parse_attachments(post)
                print(f"   Attachments: {len(attachments)}")
        
        print("\n✅ Test completed!")
    
    asyncio.run(test())

