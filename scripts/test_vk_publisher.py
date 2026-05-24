#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тест VK Publisher модуля

Проверяет:
1. Инициализацию VK Publisher
2. Публикацию тестового поста
3. Создание и публикацию дайджеста
4. Интеграцию с Production Workflow
"""
import asyncio
import logging
import sys
from datetime import datetime

# Setup logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger(__name__)

from sqlalchemy import and_, select  # noqa: E402

from config.runtime import VK_MAIN_TOKENS, VK_TEST_GROUP_ID  # noqa: E402
from database.connection import get_db_session_context  # noqa: E402
from database.models import Community, Post, Region  # noqa: E402
from modules.aggregation.aggregator import NewsAggregator  # noqa: E402
from modules.publisher.vk_publisher import VKPublisher  # noqa: E402


async def test_vk_publisher_initialization():
    """Тест 1: Инициализация VK Publisher"""
    logger.info("\n" + "=" * 60)
    logger.info("🧪 ТЕСТ 1: Инициализация VK Publisher")
    logger.info("=" * 60)

    try:
        # Используем первый доступный токен
        token = VK_MAIN_TOKENS["VALSTAN"]["token"]
        publisher = VKPublisher(token)

        logger.info("✅ VK Publisher инициализирован успешно")

        # Проверим информацию о группе
        group_info = publisher.get_group_info(VK_TEST_GROUP_ID)
        if group_info:
            logger.info(f"📋 Тестовая группа: {group_info['name']}")
            logger.info(f"🔗 URL: {group_info['url']}")
        else:
            logger.warning("⚠️ Не удалось получить информацию о группе")

        return True

    except Exception as e:
        logger.error(f"❌ Ошибка инициализации: {e}")
        return False


async def test_simple_post_publishing():
    """Тест 2: Публикация простого поста"""
    logger.info("\n" + "=" * 60)
    logger.info("🧪 ТЕСТ 2: Публикация простого поста")
    logger.info("=" * 60)

    try:
        token = VK_MAIN_TOKENS["VALSTAN"]["token"]
        publisher = VKPublisher(token)

        # Тестовый пост
        test_text = f"""🧪 ТЕСТ ПУБЛИКАЦИИ SETKA

Это тестовый пост для проверки работы VK Publisher модуля.

📅 Время: {datetime.now().strftime('%d.%m.%Y %H:%M')}
🤖 Система: SETKA v1.0-beta
🔧 Модуль: VK Publisher

#Тест #SETKA #Автоматизация"""

        result = await publisher.publish_digest(
            text=test_text, target_group_id=VK_TEST_GROUP_ID, from_group=True
        )

        if result["success"]:
            logger.info("✅ Пост опубликован успешно!")
            logger.info(f"📝 Post ID: {result['post_id']}")
            logger.info(f"🔗 URL: {result['url']}")
            return True
        else:
            logger.error(f"❌ Ошибка публикации: {result['error']}")
            return False

    except Exception as e:
        logger.error(f"❌ Ошибка теста: {e}", exc_info=True)
        return False


async def test_digest_publishing():
    """Тест 3: Создание и публикация дайджеста"""
    logger.info("\n" + "=" * 60)
    logger.info("🧪 ТЕСТ 3: Создание и публикация дайджеста")
    logger.info("=" * 60)

    try:
        token = VK_MAIN_TOKENS["VALSTAN"]["token"]
        publisher = VKPublisher(token)

        # Получаем несколько постов из БД
        async with get_db_session_context() as session:
            result = await session.execute(
                select(Post)
                .join(Community)
                .where(
                    and_(
                        Post.ai_analyzed.is_(True),
                        Post.status == "new",
                        Post.date_published
                        >= datetime.now().replace(hour=0, minute=0, second=0, microsecond=0),
                    )
                )
                .limit(5)
            )
            posts = list(result.scalars().all())

        if not posts:
            logger.warning("⚠️ Нет постов для создания дайджеста")
            return False

        logger.info(f"📊 Найдено {len(posts)} постов для дайджеста")

        # Создаем дайджест
        aggregator = NewsAggregator(max_posts_per_digest=3)

        digest = await aggregator.aggregate(
            posts=posts[:3],
            title="🧪 ТЕСТОВЫЙ ДАЙДЖЕСТ SETKA",
            hashtags=["#Тест", "#SETKA", "#Дайджест"],
        )

        if not digest:
            logger.error("❌ Не удалось создать дайджест")
            return False

        logger.info(f"✅ Дайджест создан: {digest}")
        logger.info(f"📝 Текст: {digest.aggregated_text[:100]}...")

        # Публикуем дайджест
        result = await publisher.publish_aggregated_post(
            digest=digest, target_group_id=VK_TEST_GROUP_ID
        )

        if result["success"]:
            logger.info("✅ Дайджест опубликован успешно!")
            logger.info(f"📝 Post ID: {result['post_id']}")
            logger.info(f"🔗 URL: {result['url']}")
            return True
        else:
            logger.error(f"❌ Ошибка публикации дайджеста: {result['error']}")
            return False

    except Exception as e:
        logger.error(f"❌ Ошибка теста: {e}", exc_info=True)
        return False


async def test_region_publishing():
    """Тест 4: Публикация для региона"""
    logger.info("\n" + "=" * 60)
    logger.info("🧪 ТЕСТ 4: Публикация для региона")
    logger.info("=" * 60)

    try:
        token = VK_MAIN_TOKENS["VALSTAN"]["token"]
        publisher = VKPublisher(token)

        # Получаем посты для региона mi
        async with get_db_session_context() as session:
            result = await session.execute(
                select(Post)
                .join(Community)
                .join(Region)
                .where(
                    and_(
                        Region.code == "mi",
                        Post.ai_analyzed.is_(True),
                        Post.status == "new",
                        Post.date_published
                        >= datetime.now().replace(hour=0, minute=0, second=0, microsecond=0),
                    )
                )
                .limit(5)
            )
            posts = list(result.scalars().all())

        if not posts:
            logger.warning("⚠️ Нет постов для региона mi")
            return False

        logger.info(f"📊 Найдено {len(posts)} постов для региона mi")

        # Публикуем для региона
        result = await publisher.publish_to_region(
            region_code="mi", posts=posts, target_group_id=VK_TEST_GROUP_ID, max_posts=3
        )

        if result["success"]:
            logger.info("✅ Региональный дайджест опубликован успешно!")
            logger.info(f"📝 Post ID: {result['post_id']}")
            logger.info(f"🔗 URL: {result['url']}")
            return True
        else:
            logger.error(f"❌ Ошибка публикации регионального дайджеста: {result['error']}")
            return False

    except Exception as e:
        logger.error(f"❌ Ошибка теста: {e}", exc_info=True)
        return False


async def test_publisher_integration():
    """Тест 5: Интеграция с Production Workflow"""
    logger.info("\n" + "=" * 60)
    logger.info("🧪 ТЕСТ 5: Интеграция с Production Workflow")
    logger.info("=" * 60)

    try:
        from scripts.run_production_workflow import ProductionWorkflow

        # Создаем workflow с публикацией
        workflow = ProductionWorkflow()

        # Получаем VK токены
        vk_tokens = await workflow.get_vk_tokens()
        if not vk_tokens:
            logger.error("❌ Нет доступных VK токенов")
            return False

        logger.info(f"📊 Доступно {len(vk_tokens)} VK токенов")

        # Тестируем публикацию для одного региона
        result = await workflow.run_single_region(
            region_code="test", max_posts=3, publish_mode="test"
        )

        if result.get("success"):
            logger.info("✅ Workflow с публикацией выполнен успешно!")
            logger.info(f"📊 Результат: {result}")
            return True
        else:
            logger.error(f"❌ Ошибка workflow: {result}")
            return False

    except Exception as e:
        logger.error(f"❌ Ошибка интеграции: {e}", exc_info=True)
        return False


async def main():
    """Запуск всех тестов"""
    logger.info("🚀 ЗАПУСК ТЕСТОВ VK PUBLISHER")
    logger.info("=" * 60)

    tests = [
        ("Инициализация VK Publisher", test_vk_publisher_initialization),
        ("Публикация простого поста", test_simple_post_publishing),
        ("Создание и публикация дайджеста", test_digest_publishing),
        ("Публикация для региона", test_region_publishing),
        ("Интеграция с Production Workflow", test_publisher_integration),
    ]

    results = []

    for test_name, test_func in tests:
        try:
            result = await test_func()
            results.append((test_name, result))
        except Exception as e:
            logger.error(f"❌ Критическая ошибка в тесте '{test_name}': {e}")
            results.append((test_name, False))

    # Итоговый отчет
    logger.info("\n" + "=" * 60)
    logger.info("📊 ИТОГОВЫЙ ОТЧЕТ ТЕСТОВ")
    logger.info("=" * 60)

    passed = 0
    total = len(results)

    for test_name, result in results:
        status = "✅ ПРОЙДЕН" if result else "❌ ПРОВАЛЕН"
        logger.info(f"{status}: {test_name}")
        if result:
            passed += 1

    logger.info(f"\n📈 Результат: {passed}/{total} тестов пройдено")

    if passed == total:
        logger.info("🎉 ВСЕ ТЕСТЫ ПРОЙДЕНЫ УСПЕШНО!")
        logger.info("✅ VK Publisher готов к использованию!")
    else:
        logger.warning(f"⚠️ {total - passed} тестов провалено")
        logger.info("🔧 Требуется доработка")

    return passed == total


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
