#!/usr/bin/env python3
"""
Тест публикации дайджестов в главные группы регионов

Создает дайджест для региона и публикует в его главную группу VK.
"""
import argparse
import asyncio
import logging
import sys
from datetime import datetime, timedelta

from sqlalchemy import and_, select

from database.connection import AsyncSessionLocal
from database.models import Post, Region
from modules.aggregation.aggregator import NewsAggregator
from modules.publisher.vk_publisher_extended import VKPublisher

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


async def test_publish_to_region(region_code: str, max_posts: int = 5, test_mode: bool = True):
    """
    Тест публикации дайджеста в главную группу региона

    Args:
        region_code: Код региона (например, 'mi')
        max_posts: Максимальное количество постов в дайджесте
        test_mode: Если True, не публикует реально, только показывает превью
    """
    logger.info("=" * 80)
    logger.info(f"ТЕСТ ПУБЛИКАЦИИ ДАЙДЖЕСТА ДЛЯ РЕГИОНА: {region_code.upper()}")
    logger.info("=" * 80)

    async with AsyncSessionLocal() as session:
        # Получаем регион
        result = await session.execute(select(Region).where(Region.code == region_code))
        region = result.scalar_one_or_none()

        if not region:
            logger.error(f"❌ Регион '{region_code}' не найден")
            return False

        logger.info(f"✅ Регион: {region.name}")
        logger.info(f"   VK Group ID: {region.vk_group_id}")

        if not region.vk_group_id:
            logger.error("❌ У региона не настроена главная группа VK!")
            logger.error("   Запустите: python scripts/find_region_groups.py")
            return False

        logger.info(f"   🔗 URL: https://vk.com/club{abs(region.vk_group_id)}")

        # Получаем топ-посты региона за последние 24 часа
        logger.info(f"\n📊 Поиск топ-{max_posts} постов за последние 24 часа...")

        cutoff_time = datetime.now() - timedelta(hours=24)
        posts_result = await session.execute(
            select(Post)
            .where(
                and_(
                    Post.region_id == region.id,
                    Post.date_published >= cutoff_time,
                    Post.ai_analyzed.is_(True),
                )
            )
            .order_by(Post.ai_score.desc())
            .limit(max_posts * 2)
        )
        posts = list(posts_result.scalars())

        if not posts:
            logger.warning("⚠️  Посты за последние 24 часа не найдены")
            logger.info("   Ищем посты без ограничения по времени...")

            posts_result = await session.execute(
                select(Post)
                .where(and_(Post.region_id == region.id, Post.ai_analyzed.is_(True)))
                .order_by(Post.ai_score.desc())
                .limit(max_posts * 2)
            )
            posts = list(posts_result.scalars())

        if not posts:
            logger.error("❌ Нет проанализированных постов для региона!")
            return False

        logger.info(f"✅ Найдено постов: {len(posts)}")

        # Создаем дайджест
        logger.info("\n📰 Создание дайджеста...")

        aggregator = NewsAggregator(max_posts_per_digest=max_posts)

        title = f"📰 НОВОСТИ {region.name.upper()}"
        hashtags = [f"#Новости{region.code.upper()}", "#SETKA"]

        digest = await aggregator.aggregate(posts=posts[:max_posts], title=title, hashtags=hashtags)

        if not digest:
            logger.error("❌ Не удалось создать дайджест")
            return False

        logger.info("✅ Дайджест создан!")
        logger.info(f"   Постов: {digest.sources_count}")
        logger.info(f"   Просмотров: {digest.total_views}")
        logger.info(f"   Лайков: {digest.total_likes}")
        logger.info(f"   Длина текста: {len(digest.aggregated_text)} символов")

        # Показываем превью
        logger.info("\n" + "=" * 80)
        logger.info("ПРЕВЬЮ ДАЙДЖЕСТА:")
        logger.info("=" * 80)
        preview = digest.aggregated_text[:500]
        logger.info(preview + ("..." if len(digest.aggregated_text) > 500 else ""))
        logger.info("=" * 80)

        if test_mode:
            logger.info("\n⚠️  TEST MODE - публикация не выполняется")
            logger.info("   Для реальной публикации запустите с --publish")
            logger.info("\n💡 Команда для публикации:")
            logger.info(
                f"   python scripts/test_publish_to_region.py --region {region_code} --publish"
            )
            return True

        # Публикуем
        logger.info("\n📤 Публикация в VK группу...")

        publisher = VKPublisher()

        result = await publisher.publish_aggregated_post(digest=digest, group_id=region.vk_group_id)

        if result["success"]:
            logger.info("✅ УСПЕШНО ОПУБЛИКОВАНО!")
            logger.info(f"   Post ID: {result['post_id']}")
            logger.info(f"   🔗 URL: {result['url']}")
        else:
            logger.error(f"❌ Ошибка публикации: {result['error']}")
            return False

        return True


async def main():
    """Главная функция"""
    parser = argparse.ArgumentParser(
        description="Тест публикации дайджеста в главную группу региона"
    )
    parser.add_argument("--region", required=True, help="Код региона (например, mi)")
    parser.add_argument(
        "--max-posts", type=int, default=5, help="Максимум постов в дайджесте (default: 5)"
    )
    parser.add_argument(
        "--publish", action="store_true", help="Реально опубликовать (без флага - только превью)"
    )

    args = parser.parse_args()

    try:
        success = await test_publish_to_region(
            args.region, args.max_posts, test_mode=not args.publish
        )
        return 0 if success else 1
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
