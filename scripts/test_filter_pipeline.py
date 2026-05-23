#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тест модульного Filter Pipeline
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select

from database.connection import AsyncSessionLocal
from database.models import Post, Region
from modules.filters import (
    BlacklistIDFilter,
    BlacklistWordFilter,
    DateFilter,
    FilterPipeline,
    SpamPatternFilter,
    StructuralDuplicateFilter,
    TextDuplicateFilter,
    TextLengthFilter,
    TextQualityFilter,
    ViewsRequirementFilter,
)


async def test_filter_pipeline():
    """Тест Filter Pipeline"""
    print("=" * 70)
    print("🧪 Тест Filter Pipeline")
    print("=" * 70)

    async with AsyncSessionLocal() as session:
        # Загрузить тестовые посты
        result = await session.execute(select(Post).limit(20))
        posts = list(result.scalars().all())

        print(f"\n📊 Загружено постов для тестирования: {len(posts)}")

        if not posts:
            print("❌ Нет постов в БД для тестирования")
            print("💡 Запустите сначала: python scripts/test_vk_monitor.py")
            return

        # Создать Pipeline фильтров (по образцу Postopus)
        pipeline = FilterPipeline(
            [
                # Уровень 1: Быстрая отсечка
                StructuralDuplicateFilter(),  # priority=10
                DateFilter(max_age_hours=72),  # priority=11
                BlacklistIDFilter(),  # priority=12
                # Уровень 2: Структурная проверка
                TextLengthFilter(min_length=10, max_length=10000),  # priority=30
                ViewsRequirementFilter(min_views=0),  # priority=31
                # Уровень 3: Дедупликация
                TextDuplicateFilter(check_full=True, check_core=True),  # priority=40
                # Уровень 4: Черные списки
                BlacklistWordFilter(),  # priority=50
                SpamPatternFilter(),  # priority=51
                # Уровень 5: Качество
                TextQualityFilter(min_words=3),  # priority=70
            ]
        )

        # Подготовить контекст
        # Получить регион первого поста
        first_post = posts[0]
        region_result = await session.execute(
            select(Region).where(Region.id == first_post.region_id)
        )
        region = region_result.scalar_one_or_none()

        context = {
            "session": session,
            "region_id": region.id if region else None,
            "region_code": region.code if region else None,
            "is_neighbor_region": False,
        }

        print("\n🔧 Контекст:")
        print(f"  Регион: {region.name if region else 'N/A'}")
        print(f"  Код: {region.code if region else 'N/A'}")

        # Обработать посты через pipeline
        print(f"\n⚙️  Обработка через {len(pipeline.filters)} фильтров...")
        print("-" * 70)

        passed_posts, pipeline_result = await pipeline.process(posts, context)

        # Вывести результаты
        print("\n" + "=" * 70)
        print("📊 РЕЗУЛЬТАТЫ ФИЛЬТРАЦИИ")
        print("=" * 70)

        print(f"\n📥 Входящих постов: {pipeline_result.original_count}")
        print(f"✅ Прошли фильтры: {pipeline_result.passed_count}")
        print(f"❌ Отфильтровано: {pipeline_result.filtered_count}")
        print(
            f"📈 Процент отсева: {(pipeline_result.filtered_count / pipeline_result.original_count * 100):.1f}%"  # noqa: E501
        )
        print(f"⏱️  Время обработки: {pipeline_result.processing_time:.3f}с")

        # Статистика по фильтрам
        print("\n📋 Статистика по фильтрам:")
        print("-" * 70)

        for stats in pipeline_result.filter_stats:
            print(f"\n{stats['name']} (приоритет: {stats['priority']})")
            print(f"  Проверено: {stats['total_checked']}")
            print(f"  Прошло: {stats['passed']}")
            print(f"  Отфильтровано: {stats['filtered']}")
            print(f"  Процент отсева: {stats['filter_rate']}")

        # Примеры прошедших постов
        print("\n" + "=" * 70)
        print("✅ ПРОШЕДШИЕ ПОСТЫ")
        print("=" * 70)

        for i, post in enumerate(passed_posts[:5], 1):
            print(f"\n{i}. Post ID: {post.id}")
            print(f"   Регион: {context['region_code']}")
            print(f"   Текст: {post.text[:100] if post.text else 'Нет текста'}...")
            print(f"   Просмотров: {post.views}, Лайков: {post.likes}")
            if hasattr(post, "ai_score"):
                print(f"   Score: {post.ai_score}")

        print("\n" + "=" * 70)
        print("✅ Тест завершен успешно!")
        print("=" * 70)

        # Анализ результатов
        if pipeline_result.filtered_count > 0:
            print(
                f"\n💡 Из {pipeline_result.original_count} постов отфильтровано "
                f"{pipeline_result.filtered_count} ({(pipeline_result.filtered_count/pipeline_result.original_count*100):.1f}%)"  # noqa: E501
            )
            print("   Это нормально! В Postopus отсеивалось 80-90% контента.")
        else:
            print("\n⚠️  Ни один пост не был отфильтрован.")
            print("   Возможно, нужно настроить фильтры или добавить больше данных.")


async def main():
    try:
        await test_filter_pipeline()
        return 0
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
