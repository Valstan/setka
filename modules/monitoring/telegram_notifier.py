"""
Telegram Notifier - sends alerts and notifications via Telegram
"""
import asyncio
import logging
from typing import Optional
from datetime import datetime
import httpx

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """Sends notifications via Telegram bot"""
    
    def __init__(self, bot_token: str, chat_id: Optional[str] = None):
        """
        Initialize Telegram notifier
        
        Args:
            bot_token: Telegram bot token
            chat_id: Default chat ID for notifications
        """
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.api_url = f"https://api.telegram.org/bot{bot_token}"
    
    async def send_message(
        self,
        text: str,
        chat_id: Optional[str] = None,
        parse_mode: str = "HTML"
    ) -> bool:
        """
        Send message via Telegram
        
        Args:
            text: Message text
            chat_id: Chat ID (uses default if not provided)
            parse_mode: Message parse mode (HTML, Markdown)
            
        Returns:
            True if sent successfully
        """
        target_chat = chat_id or self.chat_id
        
        if not target_chat:
            logger.error("No chat ID provided")
            return False
        
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"{self.api_url}/sendMessage",
                    json={
                        "chat_id": target_chat,
                        "text": text,
                        "parse_mode": parse_mode
                    }
                )
                
                if response.status_code == 200:
                    logger.info(f"Message sent to Telegram: {text[:50]}...")
                    return True
                else:
                    logger.error(f"Telegram API error: {response.status_code} - {response.text}")
                    return False
                    
        except Exception as e:
            logger.error(f"Error sending Telegram message: {e}")
            return False
    
    async def send_error_alert(
        self,
        error_message: str,
        module: str = "SETKA",
        details: Optional[str] = None
    ) -> bool:
        """
        Send error alert
        
        Args:
            error_message: Error description
            module: Module name where error occurred
            details: Additional details
            
        Returns:
            True if sent successfully
        """
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        message = f"""🚨 <b>SETKA Error Alert</b>

📍 Module: {module}
⏰ Time: {timestamp}
❌ Error: {error_message}
"""
        
        if details:
            message += f"\n📝 Details:\n{details[:500]}"
        
        return await self.send_message(message)
    
    async def send_success_notification(
        self,
        message: str,
        module: str = "SETKA"
    ) -> bool:
        """
        Send success notification
        
        Args:
            message: Success message
            module: Module name
            
        Returns:
            True if sent successfully
        """
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        text = f"""✅ <b>SETKA Notification</b>

📍 Module: {module}
⏰ Time: {timestamp}
💬 {message}
"""
        
        return await self.send_message(text)
    
    async def send_stats_report(
        self,
        stats: dict
    ) -> bool:
        """
        Send statistics report
        
        Args:
            stats: Dictionary with statistics
            
        Returns:
            True if sent successfully
        """
        message = f"""📊 <b>SETKA Daily Report</b>

⏰ {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

"""
        
        for key, value in stats.items():
            emoji = self._get_emoji_for_stat(key)
            message += f"{emoji} {key}: {value}\n"
        
        return await self.send_message(message)
    
    def _get_emoji_for_stat(self, stat_name: str) -> str:
        """Get emoji for stat type"""
        emoji_map = {
            'regions': '🌍',
            'communities': '📡',
            'posts': '📝',
            'new_posts': '🆕',
            'analyzed': '🤖',
            'published': '📤',
            'errors': '❌',
            'warnings': '⚠️'
        }
        
        for key, emoji in emoji_map.items():
            if key in stat_name.lower():
                return emoji
        
        return '📌'

