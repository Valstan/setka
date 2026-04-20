"""
VK Publisher - Publishes digest posts to VK groups

Migrated from old_postopus bin/rw/post_msg.py and bin/rw/posting_post.py publishing logic.
Handles VK API wall.post with proper error handling and token rotation.
"""
import asyncio
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime

from utils.post_utils import lip_of_post
from utils.vk_attachments import build_attachments_list

logger = logging.getLogger(__name__)


class VKPublisher:
    """
    Publishes posts to VK groups.

    IMPORTANT: This class creates its OWN VK client with the PUBLISH token.
    Never reuse a parsing client for publishing.
    """

    # VK API limits
    POSTS_PER_DAY_LIMIT = 50  # Per group
    POST_INTERVAL_SECONDS = 5  # Minimum interval between posts

    def __init__(
        self,
        vk_client=None,
        test_polygon_mode: bool = False,
        test_polygon_group_id: int = -137760500,
    ):
        """
        Args:
            vk_client: Optional VK API client. If None, creates one with the publish token.
            test_polygon_mode: If True, post to test group instead
            test_polygon_group_id: Test polygon VK group ID
        """
        from modules.vk_monitor.vk_client import VKClient
        from config.runtime import get_publish_token

        if vk_client is not None:
            # Use provided client (for tests that already set up correctly)
            self.vk_client = vk_client
        else:
            # Create own client with PUBLISH token
            publish_token = get_publish_token()
            if not publish_token:
                raise RuntimeError(
                    "No VK publish token configured. Set VK_PUBLISH_TOKEN_NAME=VALSTAN in env."
                )
            self.vk_client = VKClient(publish_token)
            logger.info("VKPublisher: created own client with publish token")

        self.test_polygon_mode = test_polygon_mode
        self.test_polygon_group_id = test_polygon_group_id
        self._last_post_time = {}  # group_id -> datetime
    
    async def publish_digest(
        self,
        group_id: int,
        text: str,
        attachments: List[str] = None,
        copyright_url: str = None,
        from_group: bool = True,
    ) -> Dict[str, Any]:
        """
        Publish digest post to VK group.
        
        Args:
            group_id: VK group ID (negative number)
            text: Post text
            attachments: List of VK attachment strings
            copyright_url: Copyright URL for attribution
            from_group: True = post as group, False = as user
        
        Returns:
            VK API response dict with post_id, url, etc.
        """
        normalized_group_id = self._normalize_group_owner_id(group_id)

        # Determine target group
        if self.test_polygon_mode:
            target_group_id = self._normalize_group_owner_id(self.test_polygon_group_id)
            logger.info(f"🧪 TEST POLYGON MODE: Posting to test group {target_group_id}")
        else:
            target_group_id = normalized_group_id
        
        # Rate limiting
        await self._enforce_rate_limit(target_group_id)
        
        # Prepare attachments
        attachments_str = ",".join(attachments) if attachments else ""
        
        # Build wall.post parameters
        params = {
            'owner_id': target_group_id,
            'message': text,
            'from_group': 1 if from_group else 0,
        }
        
        if attachments_str:
            params['attachments'] = attachments_str
        
        if copyright_url:
            params['copyright'] = copyright_url
        
        # Execute wall.post
        try:
            response = await self._call_wall_post(params)
            
            post_id = response.get('post_id')
            post_url = f"https://vk.com/wall{target_group_id}_{post_id}"
            
            logger.info(f"✅ Published post {post_id} to group {target_group_id}")
            logger.info(f"   URL: {post_url}")
            
            # Track last post time
            self._last_post_time[target_group_id] = datetime.now()
            
            return {
                'success': True,
                'post_id': post_id,
                'owner_id': target_group_id,
                'url': post_url,
                'text_length': len(text),
                'attachments_count': len(attachments) if attachments else 0,
            }
            
        except Exception as e:
            logger.error(f"❌ Failed to publish post to group {target_group_id}: {e}")
            
            return {
                'success': False,
                'error': str(e),
                'group_id': target_group_id,
            }
    
    async def publish_repost(
        self,
        group_id: int,
        source_owner_id: int,
        source_post_id: int,
        message: str = "",
    ) -> Dict[str, Any]:
        """
        Repost from another group.
        
        Uses VK API wall.repost
        
        Args:
            group_id: Target group ID
            source_owner_id: Source post owner ID
            source_post_id: Source post ID
            message: Optional message to add
        
        Returns:
            VK API response dict
        """
        normalized_group_id = self._normalize_group_owner_id(group_id)

        if self.test_polygon_mode:
            target_group_id = self._normalize_group_owner_id(self.test_polygon_group_id)
        else:
            target_group_id = normalized_group_id
        
        # Rate limiting
        await self._enforce_rate_limit(target_group_id)
        
        # Build repost object string
        repost_object = f"wall{source_owner_id}_{source_post_id}"
        
        # VK API expects `object` (e.g. wall-123_456), not an informal `repost` key.
        params = {
            'object': repost_object,
            'message': message,
        }
        if target_group_id < 0:
            params['group_id'] = abs(target_group_id)
        
        try:
            response = await self._call_wall_post(params, method='wall.repost')
            
            post_id = response.get('post_id')
            success = response.get('success', 0) == 1
            
            if success:
                post_url = f"https://vk.com/wall{target_group_id}_{post_id}"
                logger.info(f"✅ Reposted {source_owner_id}_{source_post_id} to {target_group_id}")
                
                self._last_post_time[target_group_id] = datetime.now()
                
                return {
                    'success': True,
                    'post_id': post_id,
                    'owner_id': target_group_id,
                    'url': post_url,
                    'reposted': True,
                }
            else:
                return {
                    'success': False,
                    'error': 'VK API returned success=0',
                }
            
        except Exception as e:
            logger.error(f"❌ Failed to repost to group {target_group_id}: {e}")
            
            return {
                'success': False,
                'error': str(e),
            }
    
    async def _call_wall_post(self, params: Dict[str, Any], method: str = 'wall.post') -> Dict:
        """
        Call VK API wall.post method.
        
        Args:
            params: wall.post parameters
            method: VK API method name
        
        Returns:
            VK API response
        """
        # Use vk_client to make API call
        # This will handle token rotation automatically
        if hasattr(self.vk_client, 'api_call'):
            import asyncio, inspect
            api_call_method = getattr(self.vk_client, 'api_call')
            if inspect.iscoroutinefunction(api_call_method):
                response = await api_call_method(method, params)
            else:
                # Sync method - run in thread
                response = await asyncio.get_event_loop().run_in_executor(
                    None, api_call_method, method, params
                )
        elif hasattr(self.vk_client, 'method'):
            response = self.vk_client.method(method, params)
        else:
            raise NotImplementedError("VK client doesn't support API calls")
        
        # Check for errors
        if 'error' in response:
            error_msg = response.get('error', {}).get('error_msg', 'Unknown error')
            raise Exception(f"VK API error: {error_msg}")
        
        return response.get('response', response)
    
    async def _enforce_rate_limit(self, group_id: int):
        """Enforce minimum interval between posts to same group."""
        last_post_time = self._last_post_time.get(group_id)
        
        if last_post_time:
            elapsed = (datetime.now() - last_post_time).total_seconds()
            if elapsed < self.POST_INTERVAL_SECONDS:
                wait_time = self.POST_INTERVAL_SECONDS - elapsed
                logger.info(f"⏳ Rate limiting: waiting {wait_time:.1f}s before next post")
                await asyncio.sleep(wait_time)
    
    def is_test_mode(self) -> bool:
        """Check if running in test polygon mode."""
        return self.test_polygon_mode
    
    def get_posts_remaining_today(self, group_id: int) -> int:
        """
        Get remaining posts that can be published today.
        
        This is a simplified check - in production would track via DB.
        """
        # Simplified - would need actual tracking in production
        return self.POSTS_PER_DAY_LIMIT

    @staticmethod
    def _normalize_group_owner_id(group_id: int) -> int:
        """
        Normalize region VK group ID to owner_id format expected by wall.post/wall.repost.

        In DB and migration scripts IDs are sometimes stored as positive numbers.
        VK wall methods for groups require negative owner_id.
        """
        gid = int(group_id)
        return -abs(gid)
