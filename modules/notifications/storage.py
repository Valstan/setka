"""
Notifications Storage

Хранение уведомлений в Redis.
Уведомления хранятся 24 часа и обновляются каждый час.
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import redis

logger = logging.getLogger(__name__)


class NotificationsStorage:
    """Хранилище уведомлений в Redis"""

    def __init__(self, redis_host: str = "localhost", redis_port: int = 6379, redis_db: int = 1):
        """
        Инициализация хранилища

        Args:
            redis_host: Хост Redis
            redis_port: Порт Redis
            redis_db: Номер БД Redis (используем 1, чтобы не мешать Celery в 0)
        """
        self.redis_client = redis.Redis(
            host=redis_host, port=redis_port, db=redis_db, decode_responses=True
        )
        self.key_prefix = "setka:notifications"

    def save_notifications(
        self,
        notifications: List[Dict[str, Any]],
        notification_type: str = "suggested_posts",
        keep_if_empty: bool = False,
        keep_window_hours: int = 6,
    ) -> bool:
        """Сохранить список уведомлений в Redis.

        Args:
            notifications: Список уведомлений (может быть пустой).
            notification_type: Тип ('suggested_posts', 'unread_messages',
                'recent_comments', ...).
            keep_if_empty: Если True и новый список пустой — НЕ перезаписываем
                существующий непустой результат, пока он моложе
                `keep_window_hours` часов. Защищает от ситуации, когда
                автопроверка возвращает [] из-за временной ошибки VK API и
                стирает результат удачной ручной проверки. Через
                `keep_window_hours` пустое всё-таки записывается, чтобы UI
                не «застрял» на устаревших данных.
            keep_window_hours: Период удержания непустого результата при
                `keep_if_empty=True`. По умолчанию 6 часов.

        Returns:
            True — если записали; False — если задержали запись (keep_if_empty)
            или произошла ошибка.
        """
        try:
            key = f"{self.key_prefix}:{notification_type}"

            if keep_if_empty and not notifications:
                existing_raw = self.redis_client.get(key)
                if existing_raw:
                    try:
                        existing = json.loads(existing_raw)
                    except (ValueError, TypeError):
                        existing = None
                    if existing and existing.get("notifications"):
                        existing_ts = existing.get("timestamp")
                        if self._within_keep_window(existing_ts, keep_window_hours):
                            logger.info(
                                "Keeping previous %d %s notifications "
                                "(new result empty, prev age within %dh window)",
                                len(existing["notifications"]),
                                notification_type,
                                keep_window_hours,
                            )
                            return False

            data = {
                "timestamp": datetime.now().isoformat(),
                "notifications": notifications,
            }

            self.redis_client.setex(
                key,
                86400,  # 24 часа
                json.dumps(data, ensure_ascii=False),
            )

            logger.info(f"Saved {len(notifications)} {notification_type} notifications to Redis")
            return True

        except Exception as e:
            logger.error(f"Failed to save {notification_type} notifications: {e}")
            return False

    @staticmethod
    def _within_keep_window(timestamp_iso: Optional[str], hours: int) -> bool:
        """True if the ISO timestamp is no older than `hours` hours from now."""
        if not timestamp_iso:
            return False
        try:
            stored = datetime.fromisoformat(timestamp_iso)
        except (ValueError, TypeError):
            return False
        age = datetime.now() - stored
        return age.total_seconds() < hours * 3600

    def get_notifications(self) -> List[Dict[str, Any]]:
        """
        Получить текущие уведомления

        Returns:
            Список уведомлений
        """
        try:
            key = f"{self.key_prefix}:suggested_posts"
            data_str = self.redis_client.get(key)

            if not data_str:
                return []

            data = json.loads(data_str)
            return data.get("notifications", [])

        except Exception as e:
            logger.error(f"Failed to get notifications: {e}")
            return []

    def get_notifications_with_timestamp(self) -> Dict[str, Any]:
        """
        Получить уведомления с timestamp последнего обновления

        Returns:
            Dict с notifications и timestamp
        """
        try:
            key = f"{self.key_prefix}:suggested_posts"
            data_str = self.redis_client.get(key)

            if not data_str:
                return {"timestamp": None, "notifications": []}

            return json.loads(data_str)

        except Exception as e:
            logger.error(f"Failed to get notifications: {e}")
            return {"timestamp": None, "notifications": []}

    def get_messages_notifications(self) -> List[Dict[str, Any]]:
        """
        Получить уведомления о непрочитанных сообщениях

        Returns:
            Список уведомлений о сообщениях
        """
        try:
            key = f"{self.key_prefix}:unread_messages"
            data_str = self.redis_client.get(key)

            if not data_str:
                return []

            data = json.loads(data_str)
            return data.get("notifications", [])

        except Exception as e:
            logger.error(f"Failed to get messages notifications: {e}")
            return []

    def get_messages_denied_groups(self) -> List[Dict[str, Any]]:
        """
        Группы, по которым VK вернул access denied на messages.getConversations
        (например, у токена нет scope `messages`).

        Нужно UI чтобы отличать «нет непрочитанных» от «нет доступа».
        """
        try:
            key = f"{self.key_prefix}:unread_messages_denied"
            data_str = self.redis_client.get(key)
            if not data_str:
                return []
            data = json.loads(data_str)
            return data.get("notifications", [])
        except Exception as e:
            logger.error(f"Failed to get messages denied groups: {e}")
            return []

    def get_comments_notifications(self) -> List[Dict[str, Any]]:
        """
        Получить уведомления о свежих комментариях (за сутки)

        Returns:
            Список уведомлений о комментариях
        """
        try:
            key = f"{self.key_prefix}:recent_comments"
            data_str = self.redis_client.get(key)

            if not data_str:
                return []

            data = json.loads(data_str)
            return data.get("notifications", [])

        except Exception as e:
            logger.error(f"Failed to get comments notifications: {e}")
            return []

    def get_all_notifications(self) -> Dict[str, Any]:
        """
        Получить все уведомления (suggested posts + unread messages + recent comments)

        Returns:
            Dict с объединёнными уведомлениями:
                - suggested_posts: List
                - unread_messages: List
                - total_count: int
                - suggested_count: int
                - messages_count: int
                - comments_count: int
                - timestamp: str (НЕ перезаписывается, остается None для внешней логики)
        """
        try:
            suggested = self.get_notifications()
            messages = self.get_messages_notifications()
            messages_denied = self.get_messages_denied_groups()
            comments = self.get_comments_notifications()

            return {
                "suggested_posts": suggested,
                "unread_messages": messages,
                "unread_messages_denied": messages_denied,
                "recent_comments": comments,
                "total_count": len(suggested) + len(messages) + len(comments),
                "suggested_count": len(suggested),
                "messages_count": len(messages),
                "messages_denied_count": len(messages_denied),
                "comments_count": len(comments),
                "timestamp": None,  # Будет установлен в API endpoint
            }

        except Exception as e:
            logger.error(f"Failed to get all notifications: {e}")
            return {
                "suggested_posts": [],
                "unread_messages": [],
                "unread_messages_denied": [],
                "recent_comments": [],
                "total_count": 0,
                "suggested_count": 0,
                "messages_count": 0,
                "messages_denied_count": 0,
                "comments_count": 0,
                "timestamp": None,
            }

    # ────────────────────────────────────────────────────────────────
    # Run history (etap 3): per-type ring-buffer of recent check runs
    # ────────────────────────────────────────────────────────────────

    HISTORY_MAX_ENTRIES = 48  # 24h × 2 runs/hour worst case
    HISTORY_TTL_SECONDS = 90000  # 25h — slightly more than the window

    def save_run(
        self,
        notification_type: str,
        *,
        count: int,
        duration_seconds: float = 0.0,
        denied_count: int = 0,
        success: bool = True,
        extra: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Append a run record to the type's history list.

        Used by `tasks.celery_app.check_*` and by the manual /check-now flow.
        The list `setka:notifications:history:{type}` is bounded by
        HISTORY_MAX_ENTRIES via LPUSH+LTRIM and refreshed TTL on each push.
        """
        try:
            key = f"{self.key_prefix}:history:{notification_type}"
            entry = {
                "ts": datetime.now().isoformat(),
                "count": int(count),
                "duration_seconds": round(float(duration_seconds), 3),
                "denied_count": int(denied_count),
                "success": bool(success),
            }
            if extra:
                entry["extra"] = extra
            pipe = self.redis_client.pipeline()
            pipe.lpush(key, json.dumps(entry, ensure_ascii=False))
            pipe.ltrim(key, 0, self.HISTORY_MAX_ENTRIES - 1)
            pipe.expire(key, self.HISTORY_TTL_SECONDS)
            pipe.execute()
            return True
        except Exception as e:
            logger.error(f"Failed to save run history for {notification_type}: {e}")
            return False

    def get_recent_runs(
        self,
        notification_type: str,
        limit: int = HISTORY_MAX_ENTRIES,
    ) -> List[Dict[str, Any]]:
        """Return newest-first list of run records for the given type."""
        try:
            key = f"{self.key_prefix}:history:{notification_type}"
            raw = self.redis_client.lrange(key, 0, limit - 1) or []
            return [json.loads(r) for r in raw]
        except Exception as e:
            logger.error(f"Failed to get run history for {notification_type}: {e}")
            return []

    def get_stats(self) -> Dict[str, Any]:
        """Aggregate stats across the three notification types.

        Returns:
            {
                'types': {
                    'suggested_posts': {
                        'total_runs': N,
                        'with_results_runs': N,    # runs where count > 0
                        'total_items': N,           # sum of counts in window
                        'avg_duration_s': float,
                        'last_run_ts': ISO | None,
                        'last_run_count': int,
                    },
                    'unread_messages': {...},
                    'recent_comments': {...},
                },
                'window_hours': 24,
            }
        """
        result: Dict[str, Any] = {"types": {}, "window_hours": 24}
        for ntype in ("suggested_posts", "unread_messages", "recent_comments"):
            runs = self.get_recent_runs(ntype, limit=self.HISTORY_MAX_ENTRIES)
            with_results = [r for r in runs if r.get("count", 0) > 0]
            total_items = sum(int(r.get("count") or 0) for r in runs)
            durations = [float(r.get("duration_seconds") or 0) for r in runs]
            avg_duration = round(sum(durations) / len(durations), 3) if durations else 0.0
            last = runs[0] if runs else None
            result["types"][ntype] = {
                "total_runs": len(runs),
                "with_results_runs": len(with_results),
                "total_items": total_items,
                "avg_duration_s": avg_duration,
                "last_run_ts": last.get("ts") if last else None,
                "last_run_count": int(last.get("count") or 0) if last else 0,
            }
        return result

    # ────────────────────────────────────────────────────────────────
    # Handled marks (etap 4a): UI "Обработано" button removes the item from
    # the active list, keeps it in archive for 7 days
    # ────────────────────────────────────────────────────────────────

    HANDLED_TTL_SECONDS = 7 * 86400

    def _handled_key(self, notification_type: str, item_id) -> str:
        return f"{self.key_prefix}:handled:{notification_type}:{item_id}"

    def mark_handled(self, notification_type: str, item_id) -> bool:
        """Mark a specific notification (comment id / post id / dialog id)
        as handled by the operator. Persists for HANDLED_TTL_SECONDS so the
        UI can hide it from the active list while still showing in archive.
        """
        try:
            key = self._handled_key(notification_type, item_id)
            self.redis_client.setex(
                key,
                self.HANDLED_TTL_SECONDS,
                datetime.now().isoformat(),
            )
            return True
        except Exception as e:
            logger.error(f"Failed to mark {notification_type}:{item_id} handled: {e}")
            return False

    def unmark_handled(self, notification_type: str, item_id) -> bool:
        """Remove the handled mark (undo)."""
        try:
            self.redis_client.delete(self._handled_key(notification_type, item_id))
            return True
        except Exception as e:
            logger.error(f"Failed to unmark {notification_type}:{item_id}: {e}")
            return False

    def is_handled(self, notification_type: str, item_id) -> bool:
        try:
            return bool(self.redis_client.exists(self._handled_key(notification_type, item_id)))
        except Exception as e:
            logger.error(f"Failed to check handled state {notification_type}:{item_id}: {e}")
            return False

    def get_handled_set(self, notification_type: str) -> set:
        """All currently-handled item_ids for the given type (for UI batch render)."""
        try:
            prefix = f"{self.key_prefix}:handled:{notification_type}:"
            keys = self.redis_client.keys(f"{prefix}*") or []
            return {k[len(prefix) :] for k in keys}
        except Exception as e:
            logger.error(f"Failed to get handled set for {notification_type}: {e}")
            return set()

    # ────────────────────────────────────────────────────────────────

    def clear_notifications(self, notification_type: str = None) -> bool:
        """
        Очистить уведомления

        Args:
            notification_type: Тип для очистки ('suggested_posts', 'unread_messages', 'recent_comments' или None для всех)

        Returns:
            True если успешно
        """
        try:
            if notification_type:
                # Очистить конкретный тип
                key = f"{self.key_prefix}:{notification_type}"
                self.redis_client.delete(key)
                logger.info(f"Cleared {notification_type} notifications")
            else:
                # Очистить все
                pattern = f"{self.key_prefix}:*"
                keys = self.redis_client.keys(pattern)
                if keys:
                    self.redis_client.delete(*keys)
                logger.info(f"Cleared all notifications ({len(keys) if keys else 0} keys)")

            return True

        except Exception as e:
            logger.error(f"Failed to clear notifications: {e}")
            return False
