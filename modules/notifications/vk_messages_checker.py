"""
VK Messages Checker

Проверка непрочитанных сообщений в главных группах регионов VK.

VK API:
- messages.getConversations возвращает список диалогов
- unread_count показывает количество непрочитанных
- Нужен токен с правами на messages. Поддерживаем 2 варианта:
  1. Community access token (предпочтительно). Выпускается в
     vk.com/club{ID} → Управление → Работа с API → Создать ключ.
     Хранится в `vk_tokens.community_id`. Вызывается БЕЗ `group_id`-параметра.
  2. User token с scope `messages` и admin-правами на группу (fallback).
     Вызывается С `group_id`-параметром.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List

from vk_api.exceptions import ApiError

from modules.notifications.base_checker import BaseVKChecker

logger = logging.getLogger(__name__)


class VKMessagesChecker(BaseVKChecker):
    """Проверка непрочитанных сообщений в VK группах.

    Особенность по сравнению с suggested/comments: VK messages API ведёт себя
    по-разному в зависимости от типа токена — community-токену не нужен
    параметр `group_id` (он подразумевается), user-токену — нужен явно.
    Поэтому отдельно вызывается `_api_for` (наследовано) с двумя разными
    подписями API-вызова в `check_unread_messages`. Auto-fallback из
    `_call_with_fallback` тут не используется (специфика messages-сценария:
    при code 15 для user-token нет смысла повторять — это значит «scope
    messages не выдан»; этот случай отдельно фиксируется в `denied_groups`).
    """

    CHECKER_NAME = "VK Messages Checker"

    def check_unread_messages(self, group_id: int) -> Dict[str, Any]:
        """
        Проверить непрочитанные сообщения в группе.
        """
        positive_id = abs(int(group_id))
        api, via_community = self._api_for(group_id)
        try:
            # У community-токена параметр group_id не нужен (он подразумевается).
            # User-токен требует group_id явно.
            if via_community:
                result = api.messages.getConversations(count=200, filter="unread")
            else:
                result = api.messages.getConversations(
                    group_id=positive_id,
                    count=200,
                    filter="unread",
                )

            unread_count = result.get("count", 0)
            items = result.get("items", [])

            try:
                if via_community:
                    stats = api.messages.getConversations(count=1)
                else:
                    stats = api.messages.getConversations(group_id=positive_id, count=1)
                total_conversations = stats.get("count", 0)
            except (ApiError, Exception) as e:
                logger.debug(f"Failed to get total conversations for group {group_id}: {e}")
                total_conversations = 0

            logger.info(
                "Group %s: %s unread messages (total=%s, via=%s)",
                group_id,
                unread_count,
                total_conversations,
                "community-token" if via_community else "user-token",
            )

            # Ссылка на раздел сообщений группы
            messages_url = f"https://vk.com/gim{positive_id}"

            return {
                "has_unread": unread_count > 0,
                "unread_count": unread_count,
                "total_conversations": total_conversations,
                "group_id": group_id,
                "url": messages_url,
                "conversations": items[:5] if items else [],  # Первые 5 для preview
            }

        except ApiError as e:
            # Обработка ошибок VK API
            if e.code == 15:  # Access denied
                logger.warning(f"No access to messages for group {group_id}")
            elif e.code == 5:  # Authorization failed
                logger.error(f"Token invalid for group {group_id}")
            elif e.code == 917:  # Messages denied
                logger.warning(f"Messages are disabled for group {group_id}")
            else:
                logger.error(f"VK API error for group {group_id}: {e} (code: {e.code})")

            return {
                "has_unread": False,
                "unread_count": 0,
                "total_conversations": 0,
                "group_id": group_id,
                "error": str(e),
                "error_code": e.code,
            }

        except Exception as e:
            logger.error(f"Error checking messages for group {group_id}: {e}")
            return {
                "has_unread": False,
                "unread_count": 0,
                "total_conversations": 0,
                "group_id": group_id,
                "error": str(e),
            }

    async def check_all_region_groups(self, region_groups: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Проверить непрочитанные сообщения во всех главных группах регионов.

        Возвращает dict:
            notifications: List - группы с >0 непрочитанных
            denied_groups: List - группы где VK вернул access denied (нет scope
                messages у токена или другой блок). Нужно, чтобы UI отличал
                «нет непрочитанных» от «нет доступа» — раньше эти два состояния
                сливались в пустой список, и пользователь видел «Все проверено»,
                хотя система просто не могла читать диалоги.
        """
        notifications: List[Dict[str, Any]] = []
        denied_groups: List[Dict[str, Any]] = []

        for group_info in region_groups:
            if not group_info.get("vk_group_id"):
                continue

            result = self.check_unread_messages(group_info["vk_group_id"])

            if result.get("error_code") is not None:
                denied_groups.append(
                    {
                        "region_id": group_info["region_id"],
                        "region_name": group_info["region_name"],
                        "region_code": group_info["region_code"],
                        "vk_group_id": result["group_id"],
                        "error_code": result["error_code"],
                        "error": result.get("error", ""),
                    }
                )
                continue

            if result["has_unread"]:
                notifications.append(
                    {
                        "type": "unread_messages",
                        "region_id": group_info["region_id"],
                        "region_name": group_info["region_name"],
                        "region_code": group_info["region_code"],
                        "vk_group_id": result["group_id"],
                        "unread_count": result["unread_count"],
                        "total_conversations": result["total_conversations"],
                        "url": result["url"],
                        "checked_at": datetime.now().isoformat(),
                    }
                )
                logger.info(
                    f"💬 {group_info['region_name']}: {result['unread_count']} unread messages"
                )

        logger.info(
            "Messages check: %d groups with unread, %d groups denied",
            len(notifications),
            len(denied_groups),
        )
        return {
            "notifications": notifications,
            "denied_groups": denied_groups,
        }


if __name__ == "__main__":
    # Простой тест. Запуск: `python -m modules.notifications.vk_messages_checker`
    # (для прямого `python <path>` — нужен editable install проекта).
    import asyncio

    from config.runtime import VK_TOKENS

    async def test():
        vk_token = VK_TOKENS.get("VALSTAN")
        if not vk_token:
            print("❌ VK token not found")
            return

        checker = VKMessagesChecker(vk_token)

        print("Testing VK Messages Checker...")

        # Тест на группе Малмыж Инфо
        result = checker.check_unread_messages(-158787639)
        print(f"\nResult: {result}")

        if result["has_unread"]:
            print(f"✅ Found {result['unread_count']} unread messages!")
            print(f"   URL: {result['url']}")
        else:
            print("ℹ️  No unread messages")
            if "error" in result:
                print(f"   Error: {result['error']}")

    asyncio.run(test())
