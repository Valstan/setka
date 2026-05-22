"""
VK Publisher - публикация дайджестов в VK группы

Из Postopus LESSONS_LEARNED:
"Публикация в VK - последний и самый важный шаг"
"Форматирование должно быть идеальным"

Usage:
    publisher = VKPublisher(vk_token)
    result = await publisher.publish_digest(text, group_id)
"""

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import vk_api
from vk_api.exceptions import ApiError
from vk_api.upload import VkUpload

if TYPE_CHECKING:
    from modules.aggregation.aggregator import AggregatedPost

logger = logging.getLogger(__name__)


class VKPublisher:
    """Публикация постов в VK группы"""

    def __init__(self, vk_token: str):
        """
        Инициализация VK Publisher

        Args:
            vk_token: VK access token с правами на публикацию в группу
                     (wall, photos, groups)
        """
        try:
            self.session = vk_api.VkApi(token=vk_token)
            self.vk = self.session.get_api()
            self.upload = VkUpload(self.session)
            logger.info("VK Publisher initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize VK Publisher: {e}")
            raise

    async def publish_digest(
        self,
        text: str,
        target_group_id: int,
        attachments: Optional[List[str]] = None,
        from_group: bool = True,
    ) -> Dict[str, Any]:
        """
        Опубликовать дайджест в VK группу

        Args:
            text: Текст поста (max 4096 символов для VK)
            target_group_id: ID группы VK (отрицательное число, например -123456)
            attachments: Список вложений VK формата (photo123_456, link, etc)
            from_group: Публиковать от имени группы (True) или пользователя (False)

        Returns:
            Dict с результатом:
                - success: bool
                - post_id: int (если success=True)
                - url: str (если success=True)
                - error: str (если success=False)
        """
        try:
            # Проверяем длину текста
            if len(text) > 4096:
                logger.warning(f"Text too long ({len(text)} chars), truncating to 4096")
                text = text[:4093] + "..."

            # Публикация поста в группу
            logger.info(f"Publishing to group {target_group_id}...")

            result = await asyncio.to_thread(
                self.vk.wall.post,
                owner_id=target_group_id,
                message=text,
                attachments=",".join(attachments) if attachments else None,
                from_group=1 if from_group else 0,
            )

            post_id = result["post_id"]
            post_url = f"https://vk.com/wall{target_group_id}_{post_id}"

            logger.info(f"✅ Successfully published to VK: {post_url}")

            return {
                "success": True,
                "post_id": post_id,
                "url": post_url,
                "group_id": target_group_id,
            }

        except ApiError as e:
            logger.error(f"VK API Error: {e}")
            return {"success": False, "error": f"VK API Error: {e}", "group_id": target_group_id}
        except Exception as e:
            logger.error(f"Failed to publish: {e}", exc_info=True)
            return {"success": False, "error": str(e), "group_id": target_group_id}

    async def publish_aggregated_post(
        self, digest: "AggregatedPost", target_group_id: int
    ) -> Dict[str, Any]:
        """
        Опубликовать агрегированный пост (дайджест) из NewsAggregator

        Args:
            digest: AggregatedPost объект от NewsAggregator
            target_group_id: ID группы VK (отрицательное число)

        Returns:
            Dict с результатом публикации
        """
        try:
            # Используем готовый текст из дайджеста
            text = digest.aggregated_text

            # TODO: В будущем добавить загрузку медиа
            # Можно извлечь фото из source_posts и загрузить
            attachments = []

            logger.info("Publishing aggregated post")
            logger.info(f"Digest contains {digest.sources_count} posts")
            logger.info(f"Total views: {digest.total_views}, likes: {digest.total_likes}")

            result = await self.publish_digest(
                text=text, target_group_id=target_group_id, attachments=attachments
            )

            return result

        except Exception as e:
            logger.error(f"Failed to publish aggregated post: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def publish_to_region(
        self, region_code: str, posts: List, target_group_id: int, max_posts: int = 5
    ) -> Dict[str, Any]:
        """
        Создать и опубликовать дайджест для региона из списка постов

        Args:
            region_code: Код региона (например, 'mi', 'nolinsk')
            posts: Список Post объектов
            target_group_id: ID группы VK
            max_posts: Максимальное количество постов в дайджесте

        Returns:
            Dict с результатом публикации
        """
        try:
            from sqlalchemy import select

            from database.connection import AsyncSessionLocal
            from database.models import Region
            from modules.aggregation.aggregator import NewsAggregator

            async with AsyncSessionLocal() as session:
                # Получаем регион
                result = await session.execute(select(Region).where(Region.code == region_code))
                region = result.scalar_one_or_none()

                if not region:
                    return {"success": False, "error": f"Region {region_code} not found"}

                # Создаем дайджест
                aggregator = NewsAggregator(max_posts_per_digest=max_posts)

                title = f"📰 НОВОСТИ {region.name.upper()}"
                hashtags = [f"#Новости{region.code.upper()}"]

                digest = await aggregator.aggregate(
                    posts=posts[:max_posts], title=title, hashtags=hashtags
                )

                if not digest:
                    return {"success": False, "error": "Failed to create digest"}

                # Публикуем
                return await self.publish_aggregated_post(digest, target_group_id)

        except Exception as e:
            logger.error(f"Failed to publish to region: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    def get_target_group_id(self, region_code: str, mode: str = "test") -> int:
        """
        Получить ID целевой группы для публикации

        Args:
            region_code: Код региона (mi, nolinsk, etc)
            mode: 'test' - в тестовую группу, 'production' - в группу региона

        Returns:
            ID группы VK (отрицательное число)
        """
        from modules.region_config import RegionConfigManager

        if mode == "test":
            return RegionConfigManager.get_main_group_id("test")
        else:
            return RegionConfigManager.get_main_group_id(region_code)

    def get_group_info(self, group_id: int) -> Optional[Dict[str, Any]]:
        """
        Получить информацию о группе VK

        Args:
            group_id: ID группы (может быть положительным или отрицательным)

        Returns:
            Dict с информацией о группе или None при ошибке
        """
        try:
            # Убираем минус если есть
            positive_id = abs(group_id)

            result = self.vk.groups.getById(group_id=positive_id)

            if result:
                group = result[0]
                return {
                    "id": group["id"],
                    "name": group["name"],
                    "screen_name": group["screen_name"],
                    "type": group["type"],
                    "url": f"https://vk.com/{group['screen_name']}",
                }

            return None

        except Exception as e:
            logger.error(f"Failed to get group info: {e}")
            return None


if __name__ == "__main__":
    # Простой тест (требует токен в переменных окружения)
    import os

    async def test():
        token = os.getenv("VK_TOKEN_PUBLISH")
        if not token:
            print("❌ VK_TOKEN_PUBLISH not set")
            return

        publisher = VKPublisher(token)

        # Тестовый пост
        result = await publisher.publish_digest(
            text="🧪 Тест публикации из SETKA\n\nЭто тестовый пост.",
            target_group_id=-123456,  # Замените на реальный ID
        )

        print(f"Result: {result}")

    asyncio.run(test())
