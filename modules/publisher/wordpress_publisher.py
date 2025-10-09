"""
WordPress Publisher - publishes content to WordPress sites
"""
import asyncio
import logging
from typing import Dict, Any, Optional
import requests
from requests.auth import HTTPBasicAuth

from database.models import Post
from modules.publisher.base_publisher import BasePublisher

logger = logging.getLogger(__name__)


class WordPressPublisher(BasePublisher):
    """Publisher for WordPress platform"""
    
    def __init__(
        self,
        site_url: str,
        username: str,
        app_password: str
    ):
        """
        Initialize WordPress Publisher
        
        Args:
            site_url: WordPress site URL (e.g., https://example.com)
            username: WordPress username
            app_password: WordPress application password
        """
        super().__init__("wordpress")
        self.site_url = site_url.rstrip('/')
        self.api_url = f"{self.site_url}/wp-json/wp/v2"
        self.auth = HTTPBasicAuth(username, app_password)
    
    async def check_connection(self) -> bool:
        """Check WordPress API connection"""
        try:
            response = requests.get(
                f"{self.api_url}/users/me",
                auth=self.auth,
                timeout=10
            )
            return response.status_code == 200
        except Exception as e:
            self.logger.error(f"WordPress connection check failed: {e}")
            return False
    
    async def publish_post(
        self,
        post: Post,
        target_id: str = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Publish post to WordPress
        
        Args:
            post: Post object to publish
            target_id: Not used for WordPress (kept for interface consistency)
            **kwargs: Additional parameters:
                - status: publish, draft, pending (default: publish)
                - categories: List of category IDs
                - tags: List of tag IDs
                - featured_media: Featured image ID
                - author: Author ID
            
        Returns:
            Dictionary with publishing results
        """
        try:
            # Prepare post data
            post_data = {
                'title': self._generate_title(post),
                'content': self._format_wordpress_content(post),
                'status': kwargs.get('status', 'publish'),
                'format': 'standard'
            }
            
            # Add optional parameters
            if kwargs.get('categories'):
                post_data['categories'] = kwargs['categories']
            
            if kwargs.get('tags'):
                post_data['tags'] = kwargs['tags']
            
            if kwargs.get('author'):
                post_data['author'] = kwargs['author']
            
            # Upload and set featured image if available
            media = self.extract_media(post)
            if media['photos'] and kwargs.get('auto_featured_image', True):
                featured_id = await self._upload_featured_image(
                    media['photos'][0]
                )
                if featured_id:
                    post_data['featured_media'] = featured_id
            
            # Create post via WordPress REST API
            response = requests.post(
                f"{self.api_url}/posts",
                json=post_data,
                auth=self.auth,
                timeout=30
            )
            
            if response.status_code in [200, 201]:
                result = response.json()
                self.log_success(post.id, self.site_url, result['id'])
                
                return {
                    'success': True,
                    'platform': 'wordpress',
                    'post_id': result['id'],
                    'url': result['link']
                }
            else:
                error_msg = f"HTTP {response.status_code}: {response.text}"
                raise Exception(error_msg)
                
        except Exception as e:
            self.log_error(post.id, self.site_url, e)
            return {
                'success': False,
                'platform': 'wordpress',
                'error': str(e)
            }
    
    def _generate_title(self, post: Post) -> str:
        """
        Generate title from post text
        
        Args:
            post: Post object
            
        Returns:
            Generated title
        """
        if not post.text:
            return "Новость"
        
        # Take first sentence or first 60 characters
        text = post.text.strip()
        
        # Try to find first sentence
        for delimiter in ['. ', '! ', '? ', '\n']:
            if delimiter in text:
                title = text.split(delimiter)[0]
                if 10 < len(title) < 100:
                    return title
        
        # Fallback: first 60 chars
        if len(text) > 60:
            return text[:57] + "..."
        
        return text
    
    def _format_wordpress_content(self, post: Post) -> str:
        """
        Format content for WordPress
        
        Args:
            post: Post object
            
        Returns:
            HTML formatted content
        """
        content = []
        
        # Add main text
        if post.text:
            # Convert line breaks to <br> or <p>
            text_html = post.text.replace('\n\n', '</p><p>').replace('\n', '<br>')
            content.append(f"<p>{text_html}</p>")
        
        # Add media
        media = self.extract_media(post)
        
        # Add photos
        for photo_url in media['photos'][1:]:  # Skip first (used as featured)
            content.append(f'<img src="{photo_url}" alt="Фото" />')
        
        # Add source
        if post.community:
            source_link = f"https://vk.com/wall{post.vk_owner_id}_{post.vk_post_id}"
            source_html = f'''
                <hr>
                <p><em>Источник: {post.community.name}</em><br>
                <a href="{source_link}" target="_blank">Читать оригинал ВКонтакте</a></p>
            '''
            content.append(source_html)
        
        return '\n'.join(content)
    
    async def _upload_featured_image(self, image_url: str) -> Optional[int]:
        """
        Upload image as WordPress media
        
        Args:
            image_url: URL of image to upload
            
        Returns:
            Media ID or None
        """
        try:
            # Download image
            img_response = requests.get(image_url, timeout=10)
            img_response.raise_for_status()
            
            # Prepare upload
            filename = image_url.split('/')[-1].split('?')[0]
            if not filename.endswith(('.jpg', '.jpeg', '.png', '.gif')):
                filename += '.jpg'
            
            # Upload to WordPress
            headers = {
                'Content-Disposition': f'attachment; filename="{filename}"'
            }
            
            response = requests.post(
                f"{self.api_url}/media",
                headers=headers,
                data=img_response.content,
                auth=self.auth,
                timeout=30
            )
            
            if response.status_code in [200, 201]:
                result = response.json()
                return result['id']
            
            return None
            
        except Exception as e:
            self.logger.warning(f"Failed to upload featured image: {e}")
            return None
    
    async def update_post(
        self,
        wp_post_id: int,
        post: Post,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Update existing WordPress post
        
        Args:
            wp_post_id: WordPress post ID
            post: Post object with new content
            **kwargs: Additional parameters
            
        Returns:
            Dictionary with update results
        """
        try:
            post_data = {
                'title': self._generate_title(post),
                'content': self._format_wordpress_content(post)
            }
            
            response = requests.post(
                f"{self.api_url}/posts/{wp_post_id}",
                json=post_data,
                auth=self.auth,
                timeout=30
            )
            
            if response.status_code == 200:
                result = response.json()
                return {
                    'success': True,
                    'platform': 'wordpress',
                    'post_id': result['id'],
                    'url': result['link']
                }
            else:
                raise Exception(f"HTTP {response.status_code}")
                
        except Exception as e:
            return {
                'success': False,
                'platform': 'wordpress',
                'error': str(e)
            }
    
    async def delete_post(self, wp_post_id: int) -> bool:
        """
        Delete WordPress post
        
        Args:
            wp_post_id: WordPress post ID
            
        Returns:
            True if successful
        """
        try:
            response = requests.delete(
                f"{self.api_url}/posts/{wp_post_id}",
                auth=self.auth,
                timeout=10
            )
            return response.status_code == 200
        except Exception as e:
            self.logger.error(f"Failed to delete post {wp_post_id}: {e}")
            return False

