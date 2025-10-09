"""
Publisher module for SETKA project
Publishes content to VK, Telegram, and WordPress
"""
from .publisher import ContentPublisher
from .vk_publisher import VKPublisher
from .telegram_publisher import TelegramPublisher
from .wordpress_publisher import WordPressPublisher

__all__ = [
    'ContentPublisher',
    'VKPublisher', 
    'TelegramPublisher',
    'WordPressPublisher'
]

