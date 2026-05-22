"""Telegram alerts for VK notifications.

Was a method on UnifiedNotificationsChecker; extracted into a module-level
function so it can be reused by Celery tasks and the API endpoint without
the wrapper class.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict

logger = logging.getLogger(__name__)


def _build_reply_keyboard(
    dashboard_url: str,
    notifications_data: Dict[str, Any],
) -> Any:
    """Build an InlineKeyboardMarkup with shortcut buttons to the SETKA UI.

    Buttons are URL-buttons (no webhook required) — клик в Telegram открывает
    `/notifications` или `/notifications#section=...`, страница ловит хеш и
    скроллит к нужному разделу. Если у бота нет inline-функционала или
    `telegram` модуль недоступен — возвращаем None и сообщение уходит
    обычным текстом (graceful degradation).
    """
    try:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    except ImportError:
        return None

    rows = []
    suggested_count = int(notifications_data.get("suggested_count") or 0)
    messages_count = int(notifications_data.get("messages_count") or 0)
    comments_count = int(notifications_data.get("comments_count") or 0)

    base = dashboard_url.rstrip("#?")

    primary_row = [InlineKeyboardButton("📬 Открыть кабинет", url=base)]
    rows.append(primary_row)

    second_row = []
    if messages_count > 0:
        second_row.append(InlineKeyboardButton(
            f"💬 Ответить ({messages_count})",
            url=f"{base}#section=messages",
        ))
    if comments_count > 0:
        second_row.append(InlineKeyboardButton(
            f"💭 Комменты ({comments_count})",
            url=f"{base}#section=comments",
        ))
    if suggested_count > 0:
        second_row.append(InlineKeyboardButton(
            f"📝 Предложки ({suggested_count})",
            url=f"{base}#section=suggested",
        ))
    if second_row:
        rows.append(second_row)

    return InlineKeyboardMarkup(rows)


async def send_telegram_notifications_alert(
    *,
    bot_token: str,
    chat_id: str,
    notifications_data: Dict[str, Any],
    dashboard_url: str,
) -> bool:
    """Send a single Telegram alert summarising current VK notifications.

    `notifications_data` shape (loosely):
        suggested_count: int
        messages_count: int
        comments_count: int   (optional, для отдельной кнопки)
        suggested_posts: list[{region_name, suggested_count, ...}]
        unread_messages: list[{region_name, unread_count, ...}]

    Returns True on success.
    """
    try:
        from telegram import Bot
    except ImportError:
        logger.warning("python-telegram-bot not installed, skipping alert")
        return False

    suggested_count = int(notifications_data.get("suggested_count") or 0)
    messages_count = int(notifications_data.get("messages_count") or 0)
    total = int(notifications_data.get("total_count") or 0)

    parts = ["📬 <b>Новые уведомления SETKA</b>\n"]

    if suggested_count > 0:
        parts.append(f"📝 Предложенных постов: <b>{suggested_count}</b>")
        for n in (notifications_data.get("suggested_posts") or [])[:5]:
            parts.append(
                f"  • {n.get('region_name', '?')}: {n.get('suggested_count', 0)} пост(ов)"
            )
        if suggested_count > 5:
            parts.append(f"  ... и ещё {suggested_count - 5} регион(ов)")

    if messages_count > 0:
        parts.append(f"\n💬 Непрочитанных сообщений: <b>{messages_count}</b>")
        for n in (notifications_data.get("unread_messages") or [])[:5]:
            parts.append(
                f"  • {n.get('region_name', '?')}: {n.get('unread_count', 0)} сообщ."
            )
        if messages_count > 5:
            parts.append(f"  ... и ещё {messages_count - 5} регион(ов)")

    parts.append(f"\n🕐 Проверено: {datetime.now().strftime('%H:%M')}")
    message = "\n".join(parts)

    reply_markup = _build_reply_keyboard(dashboard_url, notifications_data)

    try:
        bot = Bot(token=bot_token)
        await bot.send_message(
            chat_id=chat_id,
            text=message,
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=reply_markup,
        )
        logger.info("✅ Telegram notification sent (total: %d)", total)
        return True
    except Exception as e:
        logger.error("Failed to send Telegram notification: %s", e)
        return False
