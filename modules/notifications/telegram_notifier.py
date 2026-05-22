"""
Telegram Notifier для критических ошибок

Отправляет уведомления только при критических ошибках:
- VK API полностью недоступен
- База данных недоступна  
- Ошибка публикации в VK

НЕ отправляет при:
- Успешном выполнении (чтобы не спамить)
- Expired токенах (это норма)
- Отсеве постов фильтрами
"""

import asyncio
import logging
from typing import Any, Dict, Optional

import aiohttp

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """Telegram уведомления для критических ошибок"""

    def __init__(self, bot_token: str, chat_id: str):
        """
        Инициализация Telegram Notifier

        Args:
            bot_token: Токен Telegram бота
            chat_id: ID чата для отправки уведомлений
        """
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

        logger.info("Telegram Notifier initialized")

    async def send_error_notification(
        self, error: str, context: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Отправить уведомление о критической ошибке

        Args:
            error: Описание ошибки
            context: Дополнительный контекст (регион, время, etc)

        Returns:
            True если отправлено успешно, False если ошибка
        """
        try:
            # Формируем сообщение
            message = self._format_error_message(error, context)

            # Отправляем через Telegram API
            async with aiohttp.ClientSession() as session:
                payload = {
                    "chat_id": self.chat_id,
                    "text": message,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                }

                async with session.post(self.api_url, json=payload) as response:
                    if response.status == 200:
                        logger.info("✅ Critical error notification sent to Telegram")
                        return True
                    else:
                        logger.error(f"Failed to send Telegram notification: {response.status}")
                        return False

        except Exception as e:
            logger.error(f"Failed to send Telegram notification: {e}")
            return False

    def _format_error_message(self, error: str, context: Optional[Dict[str, Any]] = None) -> str:
        """
        Форматировать сообщение об ошибке

        Args:
            error: Описание ошибки
            context: Дополнительный контекст

        Returns:
            Отформатированное сообщение
        """
        from datetime import datetime

        import pytz

        # Текущее время в Москве
        moscow_tz = pytz.timezone("Europe/Moscow")
        now_moscow = datetime.now(moscow_tz)
        time_str = now_moscow.strftime("%H:%M:%S MSK")

        # Базовое сообщение
        message = "🚨 <b>SETKA Critical Error</b>\n\n"
        message += f"⏰ <b>Time:</b> {time_str}\n"
        message += f"❌ <b>Error:</b> {error}\n"

        # Добавляем контекст если есть
        if context:
            message += "\n📋 <b>Context:</b>\n"

            if "region_code" in context:
                message += f"🌍 Region: {context['region_code']}\n"

            if "task_name" in context:
                message += f"🔧 Task: {context['task_name']}\n"

            if "posts_count" in context:
                message += f"📊 Posts: {context['posts_count']}\n"

            if "duration" in context:
                message += f"⏱️ Duration: {context['duration']}s\n"

        # Добавляем ссылку на дашборд
        message += "\n🔗 <a href='https://3931b3fe50ab.vps.myjino.ru/'>Dashboard</a>"

        return message

    async def send_workflow_summary(self, stats: Dict[str, Any]) -> bool:
        """
        НЕ ИСПОЛЬЗОВАТЬ - чтобы не спамить

        Этот метод оставлен для будущего использования,
        но по умолчанию не отправляет уведомления
        """
        logger.info("Workflow summary notification skipped (to avoid spam)")
        return True

    async def test_connection(self) -> bool:
        """
        Проверить соединение с Telegram API

        Returns:
            True если соединение работает, False если ошибка
        """
        try:
            async with aiohttp.ClientSession() as session:
                payload = {
                    "chat_id": self.chat_id,
                    "text": "🧪 SETKA Telegram connection test",
                    "parse_mode": "HTML",
                }

                async with session.post(self.api_url, json=payload) as response:
                    if response.status == 200:
                        logger.info("✅ Telegram connection test successful")
                        return True
                    else:
                        logger.error(f"Telegram connection test failed: {response.status}")
                        return False

        except Exception as e:
            logger.error(f"Telegram connection test failed: {e}")
            return False


def get_telegram_notifier() -> Optional[TelegramNotifier]:
    """
    Получить экземпляр TelegramNotifier из конфигурации

    Returns:
        TelegramNotifier или None если конфигурация не найдена
    """
    try:
        from config.runtime import TELEGRAM_ALERT_CHAT_ID, TELEGRAM_TOKENS

        bot_token = TELEGRAM_TOKENS.get("VALSTANBOT")
        chat_id = TELEGRAM_ALERT_CHAT_ID

        if not bot_token or not chat_id:
            logger.warning("Telegram credentials not configured")
            return None

        return TelegramNotifier(bot_token, chat_id)

    except Exception as e:
        logger.error(f"Failed to create TelegramNotifier: {e}")
        return None


if __name__ == "__main__":
    # Простой тест
    async def test():
        notifier = get_telegram_notifier()
        if not notifier:
            print("❌ TelegramNotifier not configured")
            return

        # Тест соединения
        success = await notifier.test_connection()
        if success:
            print("✅ Telegram connection test passed")
        else:
            print("❌ Telegram connection test failed")

    asyncio.run(test())
