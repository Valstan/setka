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


def _extract_photo_urls(attachments) -> List[str]:
    """Достать ссылку на самую крупную версию каждого фото-вложения (для показа)."""
    urls: List[str] = []
    for att in attachments or []:
        if att.get("type") != "photo":
            continue
        sizes = (att.get("photo") or {}).get("sizes") or []
        if not sizes:
            continue
        best = max(sizes, key=lambda s: s.get("width", 0) or 0)
        if best.get("url"):
            urls.append(best["url"])
    return urls


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

    def fetch_suggested_posts(self, group_id: int) -> List[Dict[str, Any]]:
        """Полный список предложенных постов с нормализованным автором.

        В отличие от ``check_suggested_posts`` (только count) — для рекламного
        кабинета: ``extended=1`` даёт ``profiles``/``groups`` в одном вызове,
        автор резолвится без N доп. ``users.get``. Не бросает наружу — при
        ошибке VK возвращает ``[]`` (скан не должен падать на одной группе).
        """

        def call(api):
            return api.wall.get(owner_id=group_id, filter="suggests", count=100, extended=1)

        try:
            result, via = self._call_with_fallback(group_id, "wall.get(suggests,extended)", call)
        except ApiError as e:
            logger.warning(f"fetch_suggested_posts group {group_id}: {e}")
            return []
        except Exception as e:
            logger.error(f"fetch_suggested_posts group {group_id}: {e}")
            return []

        items = result.get("items", []) or []
        profiles = {p["id"]: p for p in (result.get("profiles") or [])}
        groups = {g["id"]: g for g in (result.get("groups") or [])}

        parsed: List[Dict[str, Any]] = []
        for item in items:
            try:
                parsed.append(self.parse_suggested_item(item, profiles, groups, group_id))
            except Exception as e:
                logger.warning(f"parse suggested item failed (group {group_id}): {e}")

        logger.info(f"Group {group_id}: fetched {len(parsed)} suggested posts (via {via})")
        return parsed

    @staticmethod
    def parse_suggested_item(
        item: Dict[str, Any],
        profiles: Dict[int, Dict[str, Any]],
        groups: Dict[int, Dict[str, Any]],
        owner_id: int,
    ) -> Dict[str, Any]:
        """Нормализовать предложенный пост: автор + цель для ЛС (R1).

        Определение автора (порядок важен):
        1) положительный ``signer_id`` → человек подписал пост, ``peer_id`` = он;
        2) иначе ``from_id > 0`` → автор-пользователь;
        3) иначе (``from_id < 0``) → автор-группа: ЛС невозможно (``peer_id`` —
           группа), помечаем ``author_is_group``.
        """
        from_id = item.get("from_id")
        signer_id = item.get("signer_id")

        if signer_id and int(signer_id) > 0:
            peer_id = int(signer_id)
            author_vk_id = int(from_id) if from_id is not None else peer_id
            is_group = False
        elif from_id is not None and int(from_id) > 0:
            peer_id = int(from_id)
            author_vk_id = int(from_id)
            is_group = False
        else:
            author_vk_id = int(from_id) if from_id is not None else None
            peer_id = author_vk_id
            is_group = True

        author_name = None
        if not is_group and peer_id:
            prof = profiles.get(peer_id)
            if prof:
                author_name = (
                    " ".join(
                        x for x in [prof.get("first_name"), prof.get("last_name")] if x
                    ).strip()
                    or None
                )
        elif is_group and author_vk_id:
            grp = groups.get(abs(author_vk_id))
            if grp:
                author_name = grp.get("name")

        attachments = item.get("attachments", []) or []
        return {
            "vk_post_id": item.get("id"),
            "community_vk_id": owner_id,
            "from_id": from_id,
            "signer_id": signer_id,
            "author_vk_id": author_vk_id,
            "peer_id": peer_id,
            "author_is_group": is_group,
            "author_name": author_name,
            "text": item.get("text", "") or "",
            "marked_as_ads": bool(item.get("marked_as_ads", 0)),
            "attachments": attachments,
            "photo_urls": _extract_photo_urls(attachments),
            "date": item.get("date"),
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
