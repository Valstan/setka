"""
Trending Topics Detection - обнаружение трендовых тем
Находит темы, которые trending в нескольких регионах одновременно
"""

import sys

sys.path.insert(0, "/home/valstan/SETKA")

import logging  # noqa: E402
import re  # noqa: E402
from collections import Counter  # noqa: E402
from datetime import datetime, timedelta  # noqa: E402
from typing import Dict, List, Set  # noqa: E402

from sqlalchemy import and_, select  # noqa: E402

from database.connection import AsyncSessionLocal  # noqa: E402
from database.models import Post, Region  # noqa: E402

logger = logging.getLogger(__name__)


class TrendingTopicsDetector:
    """
    Обнаружение trending тем across регионов

    Использует:
    - Частотный анализ слов
    - Кластеризацию похожих постов
    - Cross-region analysis
    """

    # Стоп-слова (не учитываем при анализе)
    STOP_WORDS = {
        "в",
        "на",
        "и",
        "с",
        "по",
        "для",
        "от",
        "до",
        "из",
        "у",
        "о",
        "об",
        "это",
        "как",
        "так",
        "то",
        "все",
        "всё",
        "вы",
        "мы",
        "он",
        "она",
        "они",
        "что",
        "который",
        "которая",
        "которые",
        "этот",
        "эта",
        "эти",
        "будет",
        "была",
        "было",
        "были",
        "есть",
        "быть",
    }

    async def detect_trending_topics(
        self, hours: int = 24, min_posts: int = 3, min_regions: int = 2
    ) -> List[Dict]:
        """
        Найти trending темы

        Args:
            hours: Период для анализа (часов назад)
            min_posts: Минимум постов для считания trending
            min_regions: Минимум регионов для cross-region trending

        Returns:
            List trending тем
        """
        logger.info(
            f"Detecting trending topics (last {hours}h, min {min_posts} posts, {min_regions} regions)"  # noqa: E501
        )

        # Получить недавние посты
        posts = await self._get_recent_posts(hours)

        if len(posts) < min_posts:
            logger.info(f"Not enough posts ({len(posts)})")
            return []

        logger.info(f"Analyzing {len(posts)} posts...")

        # Извлечь ключевые слова из всех постов
        keywords_by_post = {}
        all_keywords = Counter()

        for post in posts:
            keywords = self._extract_keywords(post.text)
            keywords_by_post[post.id] = keywords
            all_keywords.update(keywords)

        # Найти топ ключевые слова (potential topics)
        top_keywords = [word for word, count in all_keywords.most_common(50) if count >= min_posts]

        logger.info(f"Found {len(top_keywords)} potential topics")

        # Для каждого ключевого слова, найти посты и регионы
        trending_topics = []

        for keyword in top_keywords:
            # Найти посты с этим keyword
            posts_with_keyword = [
                post for post in posts if keyword in keywords_by_post.get(post.id, set())
            ]

            # Получить регионы
            regions = set(post.region_id for post in posts_with_keyword)

            # Проверить критерии trending
            if len(posts_with_keyword) >= min_posts and len(regions) >= min_regions:
                # Вычислить engagement
                total_engagement = sum(
                    post.views + post.likes * 2 + post.reposts * 5 for post in posts_with_keyword
                )

                trending_topics.append(
                    {
                        "keyword": keyword,
                        "post_count": len(posts_with_keyword),
                        "region_count": len(regions),
                        "regions": list(regions),
                        "total_engagement": total_engagement,
                        "avg_engagement": total_engagement / len(posts_with_keyword),
                        "sample_posts": [
                            {"id": p.id, "text": p.text[:100] if p.text else "", "views": p.views}
                            for p in sorted(
                                posts_with_keyword, key=lambda x: x.views, reverse=True
                            )[:3]
                        ],
                    }
                )

        # Сортировать по engagement
        trending_topics.sort(key=lambda t: t["total_engagement"], reverse=True)

        logger.info(f"Found {len(trending_topics)} trending topics")

        return trending_topics

    async def _get_recent_posts(self, hours: int) -> List:
        """Получить недавние посты"""
        async with AsyncSessionLocal() as session:
            cutoff_time = datetime.utcnow() - timedelta(hours=hours)

            result = await session.execute(
                select(Post)
                .where(
                    and_(
                        Post.date_published >= cutoff_time,
                        Post.ai_analyzed.is_(True),
                        Post.status != "rejected",
                    )
                )
                .order_by(Post.date_published.desc())
            )

            posts = result.scalars().all()
            return list(posts)

    def _extract_keywords(self, text: str, min_length: int = 4) -> Set[str]:
        """
        Извлечь ключевые слова из текста

        Args:
            text: Текст
            min_length: Минимальная длина слова

        Returns:
            Set ключевых слов
        """
        if not text:
            return set()

        # Lowercase и очистка
        text = text.lower()

        # Удалить URL
        text = re.sub(r"http\S+|www.\S+", "", text)

        # Удалить спецсимволы
        text = re.sub(r"[^\w\s]", " ", text)

        # Разбить на слова
        words = text.split()

        # Фильтровать
        keywords = {
            word
            for word in words
            if len(word) >= min_length and word not in self.STOP_WORDS and not word.isdigit()
        }

        return keywords

    async def get_region_specific_trends(
        self, region_code: str, hours: int = 24, limit: int = 10
    ) -> List[Dict]:
        """
        Получить trending темы для конкретного региона

        Args:
            region_code: Код региона
            hours: Период анализа
            limit: Максимум тем

        Returns:
            List trending тем для региона
        """
        async with AsyncSessionLocal() as session:
            # Получить регион
            result = await session.execute(select(Region).where(Region.code == region_code))
            region = result.scalar_one_or_none()

            if not region:
                return []

            # Получить недавние посты региона
            cutoff_time = datetime.utcnow() - timedelta(hours=hours)

            result = await session.execute(
                select(Post).where(
                    and_(
                        Post.region_id == region.id,
                        Post.date_published >= cutoff_time,
                        Post.ai_analyzed.is_(True),
                    )
                )
            )

            posts = result.scalars().all()

            if not posts:
                return []

            # Извлечь keywords
            all_keywords = Counter()
            for post in posts:
                keywords = self._extract_keywords(post.text)
                all_keywords.update(keywords)

            # Топ keywords
            trending = []
            for keyword, count in all_keywords.most_common(limit):
                # Найти посты с этим keyword
                posts_with_keyword = [p for p in posts if keyword in self._extract_keywords(p.text)]

                total_engagement = sum(
                    p.views + p.likes * 2 + p.reposts * 5 for p in posts_with_keyword
                )

                trending.append(
                    {
                        "keyword": keyword,
                        "count": count,
                        "engagement": total_engagement,
                        "sample_post": (
                            posts_with_keyword[0].text[:100] if posts_with_keyword else ""
                        ),
                    }
                )

            return trending


if __name__ == "__main__":
    import asyncio

    async def test():
        detector = TrendingTopicsDetector()

        print("=" * 60)
        print("🧪 Testing Trending Topics Detector")
        print("=" * 60)

        # Test 1: Detect trending (last 24h)
        print("\n1. Detecting trending topics (last 24 hours)...")
        topics = await detector.detect_trending_topics(hours=24, min_posts=2, min_regions=1)

        if topics:
            print(f"\n   Found {len(topics)} trending topics:")
            for i, topic in enumerate(topics[:5], 1):
                print(f"\n   {i}. \"{topic['keyword']}\"")
                print(f"      Posts: {topic['post_count']}, Regions: {topic['region_count']}")
                print(f"      Engagement: {topic['total_engagement']}")
        else:
            print("   No trending topics found")

        # Test 2: Region-specific trends
        print("\n2. Region-specific trends (mi)...")
        region_trends = await detector.get_region_specific_trends("mi", hours=72, limit=5)

        if region_trends:
            print(f"\n   Found {len(region_trends)} trending keywords:")
            for i, trend in enumerate(region_trends, 1):
                print(
                    f"   {i}. \"{trend['keyword']}\" (count: {trend['count']}, engagement: {trend['engagement']})"  # noqa: E501
                )
        else:
            print("   No trends found")

        print("\n✅ Test completed!")

    asyncio.run(test())
