#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тест модуля агрегации новостей
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select  # noqa: E402

from database.connection import AsyncSessionLocal  # noqa: E402
from database.models import Post  # noqa: E402
from modules.aggregation import NewsAggregator, PostClusterer  # noqa: E402


async def test_aggregation():
    """Тест агрегации новостей"""
    print("=" * 70)
    print("🧪 Тест агрегации новостей")
    print("=" * 70)

    async with AsyncSessionLocal() as session:
        # Загрузить посты
        result = await session.execute(select(Post).where(Post.ai_category == "novost").limit(10))
        posts = list(result.scalars().all())

        if not posts:
            # Загрузить любые посты
            result = await session.execute(select(Post).limit(10))
            posts = list(result.scalars().all())

        print(f"\n📊 Загружено постов: {len(posts)}")

        if len(posts) < 2:
            print("⚠️  Недостаточно постов для агрегации (нужно минимум 2)")
            return

        # Сортируем по просмотрам (как в Postopus!)
        posts.sort(key=lambda p: p.views, reverse=True)

        print("\n📋 Топ постов по просмотрам:")
        for i, post in enumerate(posts[:5], 1):
            print(f"{i}. ID:{post.id} - {post.views} просмотров, {post.likes} лайков")
            print(f"   {post.text[:60] if post.text else 'Нет текста'}...")

        # Создать агрегатор
        aggregator = NewsAggregator(
            max_posts_per_digest=5, max_text_length=4000, max_media_items=10
        )

        # Агрегировать
        print("\n⚙️  Агрегация...")
        digest = await aggregator.aggregate(
            posts, title="📰 НОВОСТИ ДНЯ", hashtags=["#НовостиМалмыж", "#Малмыж"]
        )

        if digest:
            print("\n✅ Создан дайджест!")
            print("=" * 70)
            print(digest.aggregated_text)
            print("=" * 70)

            print("\n📊 Статистика дайджеста:")
            print(f"  Постов объединено: {digest.sources_count}")
            print(f"  Якорь: Post ID {digest.anchor_post.id}")
            print(f"  Дополнительных: {len(digest.additional_posts)}")
            print(f"  Всего просмотров: {digest.total_views}")
            print(f"  Всего лайков: {digest.total_likes}")
            print(f"  Категории: {', '.join(digest.categories)}")

        # Тест кластеризации
        print(f"\n\n{'='*70}")
        print("🧪 Тест кластеризации")
        print("=" * 70)

        clusterer = PostClusterer(time_window_hours=24, min_cluster_size=2)
        clusters = await clusterer.cluster_posts(posts, by_category=True, by_time=True)

        print(f"\n✅ Создано кластеров: {len(clusters)}")

        for i, cluster in enumerate(clusters, 1):
            print(f"\nКластер {i}: {len(cluster)} постов")
            for post in cluster:
                print(f"  - ID:{post.id}, {post.ai_category or 'novost'}, {post.views} views")

        # Агрегация по категориям
        print(f"\n\n{'='*70}")
        print("🧪 Тест агрегации по категориям")
        print("=" * 70)

        digests = await aggregator.aggregate_by_category(posts, max_digests=3)

        print(f"\n✅ Создано дайджестов: {len(digests)}")

        for i, digest in enumerate(digests, 1):
            print(f"\nДайджест {i}:")
            print(f"  Категории: {', '.join(digest.categories)}")
            print(f"  Постов: {digest.sources_count}")
            print(f"  Просмотров: {digest.total_views}")
            print(f"  Длина текста: {len(digest.aggregated_text)} символов")

        print(f"\n{'='*70}")
        print("✅ Тест агрегации завершен!")
        print("=" * 70)


async def main():
    try:
        await test_aggregation()
        return 0
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
