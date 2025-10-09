"""
Telegram Publisher - publishes content to Telegram channels
"""
import asyncio
import logging
from typing import Dict, Any, Optional, List
from telegram import Bot, InputMediaPhoto
from telegram.error import TelegramError
from telegram.constants import ParseMode
import requests

from database.models import Post
from modules.publisher.base_publisher import BasePublisher

logger = logging.getLogger(__name__)


class TelegramPublisher(BasePublisher):
    """Publisher for Telegram platform"""
    
    def __init__(self, bot_token: str):
        """
        Initialize Telegram Publisher
        
        Args:
            bot_token: Telegram Bot API token
        """
        super().__init__("telegram")
        self.bot_token = bot_token
        self.bot = None
        self._initialize_bot()
    
    def _initialize_bot(self):
        """Initialize Telegram Bot"""
        try:
            self.bot = Bot(token=self.bot_token)
            self.logger.info("Telegram Bot initialized successfully")
        except Exception as e:
            self.logger.error(f"Failed to initialize Telegram Bot: {e}")
            raise
    
    async def check_connection(self) -> bool:
        """Check Telegram Bot connection"""
        try:
            bot_info = await self.bot.get_me()
            self.logger.info(f"Telegram Bot: @{bot_info.username}")
            return True
        except Exception as e:
            self.logger.error(f"Telegram connection check failed: {e}")
            return False
    
    async def publish_post(
        self,
        post: Post,
        target_id: str,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Publish post to Telegram channel
        
        Args:
            post: Post object to publish
            target_id: Telegram channel ID or @username
            **kwargs: Additional parameters:
                - parse_mode: HTML, Markdown, or MarkdownV2
                - disable_notification: Silent message
                - disable_web_page_preview: Disable link preview
            
        Returns:
            Dictionary with publishing results
        """
        try:
            # Prepare text with HTML formatting
            text = self._format_telegram_text(
                post,
                add_source=kwargs.get('add_source', True)
            )
            
            # Extract media
            media = self.extract_media(post)
            
            # Publish based on media type
            if media['photos']:
                result = await self._publish_with_photos(
                    target_id,
                    text,
                    media['photos'],
                    kwargs
                )
            else:
                result = await self._publish_text_only(
                    target_id,
                    text,
                    kwargs
                )
            
            if result:
                self.log_success(post.id, target_id, result.message_id)
                return {
                    'success': True,
                    'platform': 'telegram',
                    'message_id': result.message_id,
                    'url': self._get_message_url(target_id, result.message_id)
                }
            else:
                raise Exception("Failed to send message")
                
        except TelegramError as e:
            self.log_error(post.id, target_id, e)
            return {
                'success': False,
                'platform': 'telegram',
                'error': str(e)
            }
    
    def _format_telegram_text(
        self,
        post: Post,
        add_source: bool = True
    ) -> str:
        """
        Format text for Telegram with HTML markup
        
        Args:
            post: Post object
            add_source: Add source attribution
            
        Returns:
            Formatted HTML text
        """
        text = post.text or ""
        
        # Add source if requested
        if add_source and post.community:
            source_link = f"https://vk.com/wall{post.vk_owner_id}_{post.vk_post_id}"
            source_text = f'\n\n📰 <i>Источник: {post.community.name}</i>\n<a href="{source_link}">🔗 Читать на VK</a>'
            text += source_text
        
        # Escape HTML special characters in text (but not in our added HTML)
        # Note: This is simplified - in production, use proper HTML escaping
        
        return text[:4096]  # Telegram limit
    
    async def _publish_text_only(
        self,
        chat_id: str,
        text: str,
        kwargs: dict
    ):
        """Publish text-only message"""
        return await self.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=ParseMode.HTML,
            disable_notification=kwargs.get('disable_notification', False),
            disable_web_page_preview=kwargs.get('disable_web_page_preview', False)
        )
    
    async def _publish_with_photos(
        self,
        chat_id: str,
        text: str,
        photo_urls: List[str],
        kwargs: dict
    ):
        """Publish message with photos"""
        try:
            if len(photo_urls) == 1:
                # Single photo
                return await self.bot.send_photo(
                    chat_id=chat_id,
                    photo=photo_urls[0],
                    caption=text[:1024],  # Caption limit
                    parse_mode=ParseMode.HTML,
                    disable_notification=kwargs.get('disable_notification', False)
                )
            else:
                # Multiple photos (media group)
                media = []
                for i, url in enumerate(photo_urls[:10]):  # Telegram limit: 10
                    media.append(
                        InputMediaPhoto(
                            media=url,
                            caption=text[:1024] if i == 0 else None,  # Caption only on first
                            parse_mode=ParseMode.HTML if i == 0 else None
                        )
                    )
                
                results = await self.bot.send_media_group(
                    chat_id=chat_id,
                    media=media,
                    disable_notification=kwargs.get('disable_notification', False)
                )
                return results[0] if results else None
                
        except Exception as e:
            self.logger.warning(f"Failed to send photos, sending text only: {e}")
            return await self._publish_text_only(chat_id, text, kwargs)
    
    def _get_message_url(self, chat_id: str, message_id: int) -> Optional[str]:
        """
        Generate message URL
        
        Args:
            chat_id: Channel ID or username
            message_id: Message ID
            
        Returns:
            URL to message or None
        """
        if chat_id.startswith('@'):
            # Public channel
            username = chat_id[1:]  # Remove @
            return f"https://t.me/{username}/{message_id}"
        else:
            # Private channel - no direct URL
            return None
    
    async def pin_message(
        self,
        chat_id: str,
        message_id: int,
        disable_notification: bool = False
    ) -> bool:
        """
        Pin message in channel
        
        Args:
            chat_id: Channel ID
            message_id: Message ID to pin
            disable_notification: Silent pin
            
        Returns:
            True if successful
        """
        try:
            await self.bot.pin_chat_message(
                chat_id=chat_id,
                message_id=message_id,
                disable_notification=disable_notification
            )
            return True
        except TelegramError as e:
            self.logger.error(f"Failed to pin message: {e}")
            return False
    
    async def edit_message(
        self,
        chat_id: str,
        message_id: int,
        new_text: str
    ) -> bool:
        """
        Edit published message
        
        Args:
            chat_id: Channel ID
            message_id: Message ID to edit
            new_text: New text content
            
        Returns:
            True if successful
        """
        try:
            await self.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=new_text[:4096],
                parse_mode=ParseMode.HTML
            )
            return True
        except TelegramError as e:
            self.logger.error(f"Failed to edit message: {e}")
            return False

