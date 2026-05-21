"""Health-watchdog for the notifications subsystem (etap 5).

Detects suspicious patterns in the recent run history that point at broken
VK access tokens: in particular, several consecutive auto-runs returning 0
items. Fires a Telegram alert at most once per pattern occurrence (dedupe
via a Redis flag with a cool-down).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from modules.notifications.storage import NotificationsStorage

logger = logging.getLogger(__name__)


# How many consecutive zero-runs trigger the "tokens are broken" alert.
ZERO_STREAK_THRESHOLD = 3

# Cool-down so we don't spam Telegram every hour while the situation persists.
ALERT_COOLDOWN_SECONDS = 6 * 3600


def detect_zero_streaks(
    storage: Optional[NotificationsStorage] = None,
) -> Dict[str, int]:
    """For each notification type, count the number of consecutive most-recent
    runs that returned count==0.

    Returns:
        {'suggested_posts': N, 'unread_messages': N, 'recent_comments': N}
        — where N is the length of the leading zero-run streak (0 if the
        latest run was non-empty).
    """
    storage = storage or NotificationsStorage()
    streaks: Dict[str, int] = {}
    for ntype in ('suggested_posts', 'unread_messages', 'recent_comments'):
        runs: List[Dict[str, Any]] = storage.get_recent_runs(ntype)
        streak = 0
        for r in runs:
            if int(r.get('count') or 0) == 0:
                streak += 1
            else:
                break
        streaks[ntype] = streak
        try:
            from monitoring.metrics import notifications_zero_streak
            notifications_zero_streak.labels(check_type=ntype).set(streak)
        except Exception:
            pass  # metrics module optional in some envs
    return streaks


async def maybe_alert_broken_tokens(
    *,
    storage: Optional[NotificationsStorage] = None,
    telegram_token: Optional[str] = None,
    chat_id: Optional[str] = None,
    dashboard_url: Optional[str] = None,
) -> Optional[str]:
    """If any check type has zero-streak >= threshold, send a Telegram alert.

    Respects a cool-down (Redis key) so the alert fires at most once per
    ALERT_COOLDOWN_SECONDS while the bad state persists.

    Returns:
        Free-form status string ('skipped:reason' / 'alert-sent' / 'no-alert').
    """
    storage = storage or NotificationsStorage()
    streaks = detect_zero_streaks(storage)

    triggered = {k: v for k, v in streaks.items() if v >= ZERO_STREAK_THRESHOLD}
    if not triggered:
        return "no-alert"

    if not telegram_token or not chat_id:
        return "skipped:no-telegram-config"

    cooldown_key = f"{storage.key_prefix}:health_alert_cooldown"
    if storage.redis_client.get(cooldown_key):
        return "skipped:cooldown"

    try:
        from telegram import Bot
    except ImportError:
        return "skipped:telegram-lib-missing"

    parts = ["⚠️ <b>SETKA notifications: подозрение на сломанные токены</b>\n"]
    for ntype, streak in triggered.items():
        label = {
            "suggested_posts": "Предложки",
            "unread_messages": "Сообщения",
            "recent_comments": "Комменты",
        }.get(ntype, ntype)
        parts.append(f"  • {label}: {streak} автопроверок подряд вернули 0")
    parts.append(
        "\nЛибо реально ничего нет (нормально), либо токен слетел "
        "(не редкость с VK Admin app — нужно перевыпустить scope `messages`/`manage`)."
    )
    if dashboard_url:
        parts.append(f"\n🔗 <a href='{dashboard_url}'>Открыть кабинет уведомлений</a>")
    msg = "\n".join(parts)

    try:
        await Bot(token=telegram_token).send_message(
            chat_id=chat_id,
            text=msg,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        storage.redis_client.setex(cooldown_key, ALERT_COOLDOWN_SECONDS, "1")
        logger.info("Sent token-health alert for streaks: %s", triggered)
        return "alert-sent"
    except Exception as e:
        logger.error("Failed to send token-health alert: %s", e)
        return f"error:{e}"
