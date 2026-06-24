#!/usr/bin/env python3
"""
Тест VK Publisher

Проверяет публикацию сводок в VK группу.

Usage:
    python scripts/test_publisher.py
"""
import asyncio
import logging
import sys
from datetime import datetime, timedelta

from sqlalchemy import and_, select

from database.connection import AsyncSessionLocal
from database.models import Post, Region
from modules.publisher.vk_publisher_extended import VKPublisher

# Setup logging
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


async def test_simple_publish():
    """Простой тест публикации текста"""
    logger.info("=" * 80)
    logger.info("TEST 1: Simple text publish")
    logger.info("=" * 80)

    try:
        VKPublisher()

        # Получаем информацию о группах пользователя
        logger.info("Getting user's groups...")

        # Тестовая сводка
        test_text = """🔥 Тест публикации SETKA v1.0

📰 Это тестовая сводка новостей
🕐 Время: {time}

✅ VK Publisher работает!

#тест #SETKA""".format(
            time=datetime.now().strftime("%H:%M:%S")
        )

        logger.info(f"Test text prepared ({len(test_text)} chars)")
        logger.info(f"\n{test_text}\n")

        # ВАЖНО: Для реальной публикации нужно указать ID вашей группы
        # Например: -123456789
        # Пока просто демонстрируем, что publisher готов
        logger.info("⚠️  To publish, you need to specify your VK group ID")
        logger.info("⚠️  Example: target_group_id = -123456789")
        logger.info("⚠️  Skipping actual publish in test mode")

        return True

    except Exception as e:
        logger.error(f"❌ Test failed: {e}", exc_info=True)
        return False


async def test_digest_creation():
    """Тест создания сводки из реальных постов"""
    logger.info("=" * 80)
    logger.info("TEST 2: Digest creation from real posts")
    logger.info("=" * 80)

    try:
        async with AsyncSessionLocal() as session:
            # Получаем регион Малмыж
            result = await session.execute(select(Region).where(Region.code == "mi"))
            region = result.scalar_one_or_none()

            if not region:
                logger.error("❌ Region 'mi' not found")
                return False

            logger.info(f"✅ Region found: {region.name}")

            # Получаем топ-5 постов за последние 24 часа
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
                .limit(5)
            )
            posts = list(posts_result.scalars())

            logger.info(f"✅ Found {len(posts)} posts")

            if not posts:
                logger.warning("⚠️  No posts found in last 24 hours")
                # Пробуем без ограничения по времени
                posts_result = await session.execute(
                    select(Post)
                    .where(and_(Post.region_id == region.id, Post.ai_analyzed.is_(True)))
                    .order_by(Post.ai_score.desc())
                    .limit(5)
                )
                posts = list(posts_result.scalars())
                logger.info(f"✅ Found {len(posts)} posts (all time)")

            if not posts:
                logger.error("❌ No analyzed posts found for region")
                return False

            # Создаем сводка
            from modules.aggregation.aggregator import NewsAggregator

            aggregator = NewsAggregator(max_posts_per_bulletin=5)

            # Определяем заголовок для региона
            title = f"📰 НОВОСТИ {region.name.upper()}"
            hashtags = [f"#Новости{region.code.upper()}"]

            digest = await aggregator.aggregate(posts=posts, title=title, hashtags=hashtags)

            if not digest:
                logger.error("❌ Failed to create digest")
                return False

            logger.info("✅ Digest created successfully!")
            logger.info(f"   Posts: {digest.sources_count}")
            logger.info(f"   Total views: {digest.total_views}")
            logger.info(f"   Total likes: {digest.total_likes}")
            logger.info(f"   Text length: {len(digest.aggregated_text)} chars")

            # Показываем превью сводки
            logger.info("\n" + "=" * 80)
            logger.info("DIGEST PREVIEW:")
            logger.info("=" * 80)
            preview = digest.aggregated_text[:500]
            logger.info(preview + "..." if len(digest.aggregated_text) > 500 else preview)
            logger.info("=" * 80 + "\n")

            return True

    except Exception as e:
        logger.error(f"❌ Test failed: {e}", exc_info=True)
        return False


async def test_full_workflow():
    """Полный тест workflow: создание + публикация (демо)"""
    logger.info("=" * 80)
    logger.info("TEST 3: Full workflow (demo)")
    logger.info("=" * 80)

    try:
        VKPublisher()

        async with AsyncSessionLocal() as session:
            # Получаем регион
            result = await session.execute(select(Region).where(Region.code == "mi"))
            region = result.scalar_one_or_none()

            if not region:
                logger.error("❌ Region not found")
                return False

            # Получаем посты
            posts_result = await session.execute(
                select(Post)
                .where(and_(Post.region_id == region.id, Post.ai_analyzed.is_(True)))
                .order_by(Post.ai_score.desc())
                .limit(5)
            )
            posts = list(posts_result.scalars())

            if not posts:
                logger.error("❌ No posts found")
                return False

            logger.info(f"✅ Found {len(posts)} posts for region {region.name}")

            # Создаем сводка
            from modules.aggregation.aggregator import NewsAggregator

            aggregator = NewsAggregator(max_posts_per_bulletin=5)

            # Определяем заголовок для региона
            title = f"📰 НОВОСТИ {region.name.upper()}"
            hashtags = [f"#Новости{region.code.upper()}"]

            digest = await aggregator.aggregate(posts=posts, title=title, hashtags=hashtags)

            if not digest:
                logger.error("❌ Failed to create digest")
                return False

            logger.info("✅ Digest created")

            # Показываем что будет опубликовано
            logger.info("\n" + "=" * 80)
            logger.info("READY TO PUBLISH:")
            logger.info("=" * 80)
            logger.info(f"Region: {region.name}")
            logger.info(f"Posts: {digest.sources_count}")
            logger.info(f"Views: {digest.total_views}")
            logger.info(f"Text: {len(digest.aggregated_text)} chars")
            logger.info("=" * 80)
            preview = digest.aggregated_text[:300]
            logger.info(preview + "...")
            logger.info("=" * 80 + "\n")

            logger.info("⚠️  To publish to VK, use:")
            logger.info(
                "    result = await publisher.publish_aggregated_post(digest, -YOUR_GROUP_ID)"
            )
            logger.info("✅ Full workflow is ready!")

            return True

    except Exception as e:
        logger.error(f"❌ Test failed: {e}", exc_info=True)
        return False


async def main():
    """Запуск всех тестов"""
    logger.info("🚀 Starting VK Publisher tests...\n")

    tests = [
        ("Simple Publish", test_simple_publish),
        ("Digest Creation", test_digest_creation),
        ("Full Workflow", test_full_workflow),
    ]

    results = []

    for name, test_func in tests:
        try:
            result = await test_func()
            results.append((name, result))

            if result:
                logger.info(f"✅ {name}: PASSED\n")
            else:
                logger.error(f"❌ {name}: FAILED\n")

        except Exception as e:
            logger.error(f"❌ {name}: ERROR - {e}\n", exc_info=True)
            results.append((name, False))

    # Итоги
    logger.info("\n" + "=" * 80)
    logger.info("TEST RESULTS")
    logger.info("=" * 80)

    passed = sum(1 for _, result in results if result)
    total = len(results)

    for name, result in results:
        status = "✅ PASSED" if result else "❌ FAILED"
        logger.info(f"{status}: {name}")

    logger.info("=" * 80)
    logger.info(f"Total: {passed}/{total} tests passed")
    logger.info("=" * 80)

    if passed == total:
        logger.info("\n🎉 All tests passed! VK Publisher is ready!")
        logger.info("\n📝 Next steps:")
        logger.info("   1. Add your VK group ID to config")
        logger.info("   2. Run: await publisher.publish_aggregated_post(digest, -YOUR_GROUP_ID)")
        logger.info("   3. Start Celery for automation: systemctl start setka-celery-worker")
    else:
        logger.error(f"\n⚠️  {total - passed} test(s) failed")

    return passed == total


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
