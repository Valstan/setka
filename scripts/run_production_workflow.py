#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Production Workflow для SETKA

Полный цикл обработки:
1. VK мониторинг
2. Filter Pipeline
3. AI анализ
4. Scoring
5. Агрегация
6. Сохранение результатов
"""
import asyncio
import logging
import os
import sys
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("/home/valstan/SETKA/logs/production_workflow.log"),
    ],
)

logger = logging.getLogger(__name__)

from sqlalchemy import and_, select

from database.connection import AsyncSessionLocal
from database.models import Filter, Post, Region, VKToken
from modules.aggregation.aggregator import NewsAggregator
from modules.core.scoring import calculate_post_score
from modules.filters import (
    BlacklistIDFilter,
    BlacklistWordFilter,
    CategoryFilter,
    DateFilter,
    FilterPipeline,
    RegionalRelevanceFilter,
    SpamPatternFilter,
    StructuralDuplicateFilter,
    TextDuplicateFilter,
    TextLengthFilter,
    TextQualityFilter,
    ViewsRequirementFilter,
)
from modules.module_activity_notifier import (
    notify_publish_completed,
    notify_publish_started,
    notify_region_processing,
    notify_workflow_completed,
    notify_workflow_started,
)
from modules.operation_tracking import (
    end_operation_error,
    end_operation_success,
    start_filtering_operation,
    start_monitoring_operation,
    update_operation_progress,
)
from modules.vk_monitor.monitor import VKMonitor


class ProductionWorkflow:
    """
    Production workflow для обработки новостей

    Объединяет все компоненты:
    - VK Monitor: сбор постов
    - Filter Pipeline: фильтрация
    - AI Analyzer: категоризация
    - Aggregation: создание дайджестов
    """

    def __init__(self):
        self.stats = {
            "start_time": datetime.now(),
            "regions_processed": 0,
            "posts_collected": 0,
            "posts_filtered": 0,
            "posts_accepted": 0,
            "errors": [],
        }

    async def get_vk_tokens(self) -> List[str]:
        """Получить активные VK токены из БД"""
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(VKToken).where(VKToken.is_active == True))
            tokens_objs = result.scalars().all()
            tokens = [t.token for t in tokens_objs if t.token]
            logger.info(f"Loaded {len(tokens)} VK tokens")
            return tokens

    async def load_filters(self) -> Dict[str, List[str]]:
        """Загрузить фильтры из БД"""
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(Filter).where(Filter.is_active == True))
            filters = result.scalars().all()

            blacklist_words = []
            blacklist_ids = []

            for f in filters:
                if f.type == "blacklist_word":
                    blacklist_words.append(f.pattern)
                elif f.type == "blacklist_id":
                    try:
                        blacklist_ids.append(int(f.pattern))
                    except ValueError:
                        pass

            logger.info(
                f"Loaded {len(blacklist_words)} word filters, {len(blacklist_ids)} ID filters"
            )

            return {"blacklist_words": blacklist_words, "blacklist_ids": blacklist_ids}

    async def create_filter_pipeline(
        self, region: Region, filters_data: Dict[str, List[str]]
    ) -> FilterPipeline:
        """
        Создать Filter Pipeline для региона

        Порядок фильтров из Postopus:
        1. Быстрая отсечка (LIP, даты, ID)
        2. Структурная проверка (длина, просмотры)
        3. Дедупликация (текст, медиа)
        4. Черные списки (слова, паттерны)
        5. Региональная релевантность
        6. Качество (категория, текст)
        """
        pipeline = FilterPipeline(
            [
                # Уровень 1: Быстрая отсечка
                StructuralDuplicateFilter(),
                DateFilter(max_age_hours=72),
                BlacklistIDFilter(),  # Загружает из БД
                # Уровень 2: Структурная проверка
                TextLengthFilter(min_length=10, max_length=10000),
                ViewsRequirementFilter(min_views=0),
                # Уровень 3: Дедупликация
                TextDuplicateFilter(check_full=True, check_core=True),
                # Уровень 4: Черные списки
                BlacklistWordFilter(),  # Загружает из БД
                SpamPatternFilter(),
                # Уровень 5: Региональная релевантность
                RegionalRelevanceFilter(required_matches=1),  # Загружает из БД
                # Уровень 6: Качество
                CategoryFilter(allowed_categories=["novost", "kultura", "sport", "proisshestvie"]),
                TextQualityFilter(min_words=3),
            ]
        )

        return pipeline

    async def process_region(
        self,
        region_code: str,
        vk_tokens: List[str],
        filters_data: Dict[str, List[str]],
        max_posts: int = 100,
    ) -> Dict[str, Any]:
        """
        Обработать один регион

        Args:
            region_code: Код региона (mi, nolinsk, etc.)
            vk_tokens: VK токены
            filters_data: Данные фильтров
            max_posts: Максимум постов для обработки

        Returns:
            Статистика обработки
        """
        logger.info(f"\n{'='*70}")
        logger.info(f"🌍 Processing region: {region_code}")
        logger.info(f"{'='*70}")

        # Уведомляем о начале обработки региона
        notify_region_processing(region_code, "Начало обработки")

        region_stats = {
            "region_code": region_code,
            "posts_collected": 0,
            "posts_before_filter": 0,
            "posts_after_filter": 0,
            "posts_accepted": 0,
            "errors": [],
        }

        try:
            async with AsyncSessionLocal() as session:
                # Получить регион
                result = await session.execute(select(Region).where(Region.code == region_code))
                region = result.scalar_one_or_none()

                if not region:
                    error_msg = f"Region {region_code} not found"
                    logger.error(error_msg)
                    region_stats["errors"].append(error_msg)
                    return region_stats

                # Добавляем region_id в статистику
                region_stats["region_id"] = region.id

                logger.info(f"📍 Region: {region.name} (ID: {region.id})")

                # 1. Запустить VK мониторинг
                logger.info("\n🔍 Step 1: VK Monitoring...")
                notify_region_processing(region_code, "VK мониторинг")

                # Start monitoring operation tracking
                monitoring_op_id = start_monitoring_operation(
                    region_code, 0  # Will be updated after getting communities count
                )

                try:
                    monitor = VKMonitor(vk_tokens=vk_tokens)
                    scan_result = await monitor.scan_region(region_code)

                    region_stats["posts_collected"] = scan_result.get("new_posts", 0)
                    logger.info(f"✅ Collected {region_stats['posts_collected']} new posts")

                    # Update operation progress
                    update_operation_progress(
                        monitoring_op_id,
                        progress=100,
                        current_step="completed",
                        details={"posts_collected": region_stats["posts_collected"]},
                    )

                except Exception as e:
                    end_operation_error(monitoring_op_id, str(e))
                    raise
                finally:
                    end_operation_success(
                        monitoring_op_id, {"posts_collected": region_stats["posts_collected"]}
                    )

                # 2. Получить посты для фильтрации
                logger.info("\n🔍 Step 2: Loading posts for filtering...")
                notify_region_processing(region_code, "Загрузка постов для фильтрации")

                # Загрузить недавние посты (последние 24 часа)
                recent_threshold = datetime.now() - timedelta(hours=24)

                posts_result = await session.execute(
                    select(Post)
                    .where(
                        and_(
                            Post.region_id == region.id,
                            Post.date_published >= recent_threshold,
                            Post.ai_analyzed == False,  # Только необработанные
                        )
                    )
                    .limit(max_posts)
                )
                posts = list(posts_result.scalars().all())

                region_stats["posts_before_filter"] = len(posts)
                logger.info(f"📊 Loaded {len(posts)} posts for filtering")

                if not posts:
                    logger.info("ℹ️ No new posts to process")
                    return region_stats

                # 3. Создать Filter Pipeline
                logger.info("\n🔍 Step 3: Creating Filter Pipeline...")
                notify_region_processing(region_code, "Создание фильтр-пайплайна")
                pipeline = await self.create_filter_pipeline(region, filters_data)

                # 4. Применить фильтры
                logger.info("\n🔍 Step 4: Applying filters...")
                notify_region_processing(region_code, "Применение фильтров")

                # Start filtering operation tracking
                filtering_op_id = start_filtering_operation(region_code, len(posts))

                try:
                    context = {"session": session, "region": region, "content_type": "novost"}

                    filtered_posts, pipeline_result = await pipeline.process(posts, context)

                    # Update operation progress
                    update_operation_progress(
                        filtering_op_id,
                        progress=100,
                        current_step="completed",
                        details={
                            "posts_before": len(posts),
                            "posts_after": len(filtered_posts),
                            "rejection_rate": f"{100 * (1 - len(filtered_posts) / max(len(posts), 1)):.1f}%",
                        },
                    )

                except Exception as e:
                    end_operation_error(filtering_op_id, str(e))
                    raise
                finally:
                    end_operation_success(
                        filtering_op_id,
                        {"posts_before": len(posts), "posts_after": len(filtered_posts)},
                    )

                region_stats["posts_after_filter"] = len(filtered_posts)
                region_stats["posts_accepted"] = len(filtered_posts)

                logger.info("✅ Filtering complete:")
                logger.info(f"   Before: {len(posts)}")
                logger.info(f"   After:  {len(filtered_posts)}")
                logger.info(
                    f"   Rejected: {len(posts) - len(filtered_posts)} ({100 * (1 - len(filtered_posts) / max(len(posts), 1)):.1f}%)"
                )

                # 5. Обновить scoring и пометить как обработанные
                logger.info("\n🔍 Step 5: Updating scores...")
                notify_region_processing(region_code, "Обновление оценок")

                for post in filtered_posts:
                    # Пересчитать score
                    post.ai_score = calculate_post_score(
                        views=post.views or 0,
                        likes=post.likes or 0,
                        reposts=post.reposts or 0,
                        comments=post.comments or 0,
                        posted_at=post.date_published,
                        source_priority=1.0,
                        ai_category_weight=0.8 if post.ai_category == "novost" else 0.5,
                    )

                    # Пометить как обработанный
                    post.ai_analyzed = True
                    post.ai_analysis_date = datetime.now()

                await session.commit()

                logger.info(f"✅ Updated scores for {len(filtered_posts)} posts")

                # 6. Создать агрегацию (опционально)
                if len(filtered_posts) >= 3:
                    logger.info("\n🔍 Step 6: Creating aggregated digest...")

                    # Сортировать по ai_score
                    sorted_posts = sorted(
                        filtered_posts, key=lambda p: p.ai_score or 0, reverse=True
                    )
                    top_posts = sorted_posts[:5]

                    aggregator = NewsAggregator(
                        max_posts_per_digest=5, max_text_length=4000, max_media_items=10
                    )

                    digest = await aggregator.aggregate(
                        posts=top_posts,
                        title=f"📰 Новости | {region.name}",
                        hashtags=[f"#{region_code}", "#новости"],
                    )

                    if digest:
                        logger.info(
                            f"✅ Created digest with {len(digest.additional_posts) + 1} posts"
                        )
                        logger.info(f"   Total views: {digest.total_views}")
                        categories_str = ", ".join(filter(None, digest.categories))
                        logger.info(f"   Categories: {categories_str}")
                    else:
                        logger.info("ℹ️ Could not create digest")

                logger.info(f"\n✅ Region {region_code} processing complete!")
                notify_region_processing(region_code, "Обработка завершена")

        except Exception as e:
            error_msg = f"Error processing region {region_code}: {str(e)}"
            logger.error(error_msg, exc_info=True)
            region_stats["errors"].append(error_msg)

        return region_stats

    async def run(self, region_codes: Optional[List[str]] = None, max_posts_per_region: int = 100):
        """
        Запустить production workflow

        Args:
            region_codes: Список кодов регионов для обработки (None = все активные)
            max_posts_per_region: Максимум постов для обработки на регион
        """
        logger.info("\n" + "=" * 70)
        logger.info("🚀 SETKA Production Workflow")
        logger.info("=" * 70)
        logger.info(f"Start time: {self.stats['start_time']}")

        try:
            # Уведомляем о запуске workflow
            if region_codes:
                notify_workflow_started(region_codes)
            else:
                notify_workflow_started(["all_active_regions"])

            # Загрузить VK токены
            vk_tokens = await self.get_vk_tokens()

            if not vk_tokens:
                logger.error("❌ No VK tokens available!")
                logger.error("💡 Run: python scripts/add_vk_tokens.py")
                return

            # Загрузить фильтры
            filters_data = await self.load_filters()

            # Получить регионы для обработки
            async with AsyncSessionLocal() as session:
                if region_codes:
                    result = await session.execute(
                        select(Region).where(
                            and_(Region.code.in_(region_codes), Region.is_active == True)
                        )
                    )
                else:
                    result = await session.execute(select(Region).where(Region.is_active == True))

                regions = list(result.scalars().all())

            logger.info(
                f"\n📊 Will process {len(regions)} regions: {', '.join(r.code for r in regions)}"
            )

            # Обработать каждый регион
            all_region_stats = []

            for region in regions:
                region_stats = await self.process_region(
                    region_code=region.code,
                    vk_tokens=vk_tokens,
                    filters_data=filters_data,
                    max_posts=max_posts_per_region,
                )

                all_region_stats.append(region_stats)

                self.stats["regions_processed"] += 1
                self.stats["posts_collected"] += region_stats["posts_collected"]
                self.stats["posts_filtered"] += region_stats["posts_before_filter"]
                self.stats["posts_accepted"] += region_stats["posts_accepted"]
                self.stats["errors"].extend(region_stats["errors"])

                # Пауза между регионами
                await asyncio.sleep(2)

            # Итоговая статистика
            self.stats["end_time"] = datetime.now()
            self.stats["duration"] = (
                self.stats["end_time"] - self.stats["start_time"]
            ).total_seconds()

            logger.info("\n" + "=" * 70)
            logger.info("📊 WORKFLOW COMPLETE - FINAL STATISTICS")
            logger.info("=" * 70)
            logger.info(f"Duration: {self.stats['duration']:.1f} seconds")
            logger.info(f"Regions processed: {self.stats['regions_processed']}")
            logger.info(f"Posts collected from VK: {self.stats['posts_collected']}")
            logger.info(f"Posts filtered: {self.stats['posts_filtered']}")
            logger.info(f"Posts accepted: {self.stats['posts_accepted']}")

            if self.stats["posts_filtered"] > 0:
                rejection_rate = 100 * (
                    1 - self.stats["posts_accepted"] / self.stats["posts_filtered"]
                )
                logger.info(f"Overall rejection rate: {rejection_rate:.1f}%")

            if self.stats["errors"]:
                logger.warning(f"\n⚠️ Errors encountered: {len(self.stats['errors'])}")
                for error in self.stats["errors"]:
                    logger.warning(f"  - {error}")

            logger.info("\n✅ Production workflow completed successfully!")

            # Уведомляем о завершении workflow
            notify_workflow_completed(
                regions_processed=self.stats["regions_processed"],
                posts_collected=self.stats["posts_collected"],
                posts_accepted=self.stats["posts_accepted"],
                duration=self.stats["duration"],
            )

        except Exception as e:
            logger.error(f"\n❌ Workflow failed: {str(e)}", exc_info=True)
            raise

    async def run_single_region(
        self, region_code: str, max_posts: int = 100, publish_mode: str = "production"
    ) -> Dict[str, Any]:
        """
        Запуск workflow для одного региона с публикацией

        Args:
            region_code: Код региона (mi, nolinsk, etc)
            max_posts: Максимальное количество постов для обработки
            publish_mode: 'test' или 'production'

        Returns:
            Dict с результатами обработки региона
        """
        start_time = datetime.now()

        try:
            logger.info(f"\n🌍 Processing region: {region_code}")
            logger.info("=" * 60)

            # Получить VK токены и фильтры
            vk_tokens = await self.get_vk_tokens()
            filters_data = await self.load_filters()

            # Обработать регион
            region_stats = await self.process_region(
                region_code=region_code,
                vk_tokens=vk_tokens,
                filters_data=filters_data,
                max_posts=max_posts,
            )

            # Получить region_id для публикации
            region_id = region_stats.get("region_id")

            # Добавить публикацию дайджеста
            posts_published = 0
            publish_error = None

            if region_stats["posts_accepted"] > 0:
                try:
                    # Получить посты для публикации
                    async with AsyncSessionLocal() as session:
                        result = await session.execute(
                            select(Post)
                            .where(
                                and_(
                                    Post.region_id == region_id,
                                    Post.ai_analyzed == True,
                                    Post.status == "new",
                                    Post.date_published >= datetime.now() - timedelta(hours=24),
                                )
                            )
                            .order_by(Post.ai_score.desc())
                            .limit(5)
                        )
                        posts = result.scalars().all()

                    if posts:
                        # Уведомляем о начале публикации
                        notify_publish_started(region_code, len(posts))

                        # Создать дайджест
                        aggregator = NewsAggregator(max_posts_per_digest=5)

                        # Получить информацию о регионе
                        async with AsyncSessionLocal() as session:
                            result = await session.execute(
                                select(Region).where(Region.code == region_code)
                            )
                            region = result.scalar_one_or_none()

                        if region:
                            title = f"📰 НОВОСТИ {region.name.upper()}"
                            hashtags = [f"#Новости{region_code.upper()}"]

                            digest = await aggregator.aggregate(
                                posts=posts, title=title, hashtags=hashtags
                            )

                            if digest:
                                # Публикация в VK
                                from config.runtime import VK_MAIN_TOKENS
                                from modules.publisher.vk_publisher import VKPublisher

                                publisher = VKPublisher(VK_MAIN_TOKENS["VALSTAN"]["token"])
                                target_group = publisher.get_target_group_id(
                                    region_code, publish_mode
                                )

                                publish_result = await publisher.publish_aggregated_post(
                                    digest, target_group
                                )

                                if publish_result["success"]:
                                    posts_published = 1
                                    logger.info(
                                        f"✅ Published digest to VK: {publish_result['url']}"
                                    )

                                    # Уведомляем о завершении публикации
                                    notify_publish_completed(
                                        publish_result["post_id"],
                                        publish_result["url"],
                                        publish_result["group_id"],
                                    )
                                else:
                                    publish_error = publish_result["error"]
                                    logger.error(f"❌ Failed to publish: {publish_error}")

                                    # Отправить Telegram уведомление об ошибке публикации
                                    try:
                                        from modules.notifications.telegram_notifier import (
                                            get_telegram_notifier,
                                        )

                                        notifier = get_telegram_notifier()
                                        if notifier:
                                            await notifier.send_error_notification(
                                                f"Failed to publish digest for region {region_code}: {publish_error}",
                                                {
                                                    "region_code": region_code,
                                                    "task_name": "publish_digest",
                                                },
                                            )
                                    except Exception as e:
                                        logger.error(f"Failed to send Telegram notification: {e}")
                            else:
                                logger.warning("Failed to create digest")
                        else:
                            logger.error(f"Region {region_code} not found")
                    else:
                        logger.info("No posts available for publishing")

                except Exception as e:
                    publish_error = str(e)
                    logger.error(f"Publishing failed: {e}", exc_info=True)

            # Итоговая статистика
            end_time = datetime.now()
            duration = (end_time - start_time).total_seconds()

            result = {
                "success": True,
                "region_code": region_code,
                "posts_collected": region_stats["posts_collected"],
                "posts_accepted": region_stats["posts_accepted"],
                "posts_published": posts_published,
                "duration": duration,
                "publish_mode": publish_mode,
                "errors": region_stats["errors"],
            }

            if publish_error:
                result["publish_error"] = publish_error
                result["errors"].append(f"Publishing: {publish_error}")

            logger.info(f"\n✅ Region {region_code} processing complete!")
            logger.info(f"   Posts collected: {region_stats['posts_collected']}")
            logger.info(f"   Posts accepted: {region_stats['posts_accepted']}")
            logger.info(f"   Posts published: {posts_published}")
            logger.info(f"   Duration: {duration:.1f}s")

            return result

        except Exception as e:
            logger.error(f"❌ Failed to process region {region_code}: {e}", exc_info=True)
            return {
                "success": False,
                "region_code": region_code,
                "error": str(e),
                "duration": (datetime.now() - start_time).total_seconds(),
            }


async def main():
    """Main entry point"""
    import argparse

    parser = argparse.ArgumentParser(description="SETKA Production Workflow")
    parser.add_argument(
        "--regions",
        nargs="+",
        help="Region codes to process (e.g., mi nolinsk). If not specified, all active regions will be processed.",
    )
    parser.add_argument(
        "--max-posts",
        type=int,
        default=100,
        help="Maximum posts per region to process (default: 100)",
    )

    args = parser.parse_args()

    workflow = ProductionWorkflow()
    await workflow.run(region_codes=args.regions, max_posts_per_region=args.max_posts)


if __name__ == "__main__":
    asyncio.run(main())
