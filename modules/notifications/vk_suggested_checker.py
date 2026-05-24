"""
VK Suggested Posts Checker

Проверка предложенных постов в главных группах регионов VK.

VK API:
- wall.get с filter='suggests' возвращает предложенные записи
- Требуется токен с правами на управление группой

Поддерживаем 2 источника токенов:
1. community access token для каждой группы (приоритет). Снимает нагрузку
   с пользовательского токена и не упирается в права на чужой стене.
2. user-токен (fallback) — раньше единственный путь.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List

from vk_api.exceptions import ApiError

from modules.notifications.base_checker import BaseVKChecker

logger = logging.getLogger(__name__)


class VKSuggestedChecker(BaseVKChecker):
    """Проверка предложенных постов в VK группах.

    `wall.get(filter='suggests')` требует scope `manage`. Community-токены
    обычно его не имеют → BaseVKChecker автоматически делает retry через
    user-token (см. `COMMUNITY_FALLBACK_CODES`).
    """

    CHECKER_NAME = "VK Suggested Checker"

    def check_suggested_posts(self, group_id: int) -> Dict[str, Any]:
        """Проверить предложенные посты в группе."""
        positive_id = abs(group_id)

        def call(api):
            return api.wall.get(owner_id=group_id, filter="suggests", count=100)

        try:
            result, via = self._call_with_fallback(group_id, "wall.get(suggests)", call)
        except ApiError as e:
            return self._format_error(group_id, e)
        except Exception as e:
            logger.error(f"Error checking group {group_id}: {e}")
            return self._empty_result(group_id, str(e))

        count = result.get("count", 0)
        logger.info(f"Group {group_id}: {count} suggested posts (via {via})")
        return {
            "has_suggested": count > 0,
            "count": count,
            "group_id": group_id,
            "url": f"https://vk.com/club{positive_id}",
            "via": via,
        }

    def _format_error(self, group_id: int, err: ApiError) -> Dict[str, Any]:
        if err.code == 15:
            logger.warning(f"No access to suggested posts for group {group_id}")
        elif err.code == 5:
            logger.error(f"Token invalid for group {group_id}")
        else:
            logger.error(f"VK API error for group {group_id}: {err}")
        return self._empty_result(group_id, str(err))

    @staticmethod
    def _empty_result(group_id: int, error: str) -> Dict[str, Any]:
        return {
            "has_suggested": False,
            "count": 0,
            "group_id": group_id,
            "error": error,
        }

    async def check_all_region_groups(
        self, region_groups: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Проверить предложенные посты во всех главных группах регионов

        Args:
            region_groups: Список dict с полями:
                - region_id: int
                - region_name: str
                - region_code: str
                - vk_group_id: int

        Returns:
            Список уведомлений о группах с предложенными постами
        """
        notifications = []

        for group_info in region_groups:
            if not group_info.get("vk_group_id"):
                continue

            result = self.check_suggested_posts(group_info["vk_group_id"])

            if result["has_suggested"]:
                notification = {
                    "region_id": group_info["region_id"],
                    "region_name": group_info["region_name"],
                    "region_code": group_info["region_code"],
                    "vk_group_id": result["group_id"],
                    "suggested_count": result["count"],
                    "url": result["url"],
                    "checked_at": datetime.now().isoformat(),
                }
                notifications.append(notification)

                logger.info(f"📬 {group_info['region_name']}: {result['count']} suggested posts")

        logger.info(f"Found {len(notifications)} groups with suggested posts")

        return notifications


if __name__ == "__main__":
    # Простой тест. Запуск: `python -m modules.notifications.vk_suggested_checker`
    # (для прямого `python <path>` — нужен editable install проекта).
    import asyncio

    from config.runtime import VK_TOKENS

    async def test():
        vk_token = VK_TOKENS.get("VALSTAN")
        if not vk_token:
            print("❌ VK token not found")
            return

        checker = VKSuggestedChecker(vk_token)

        # Тест на одной группе (Малмыж Инфо)
        result = checker.check_suggested_posts(-158787639)
        print(f"Result: {result}")

    asyncio.run(test())
