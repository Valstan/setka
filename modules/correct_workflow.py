"""
Correct Workflow System - Правильная логика работы SETKA

Логика:
1. Получить текущую тематику по расписанию
2. Найти сообщества этой тематики для региона
3. Собрать посты из этих сообществ за последние 3 дня
4. Применить фильтры системы
5. Создать дайджест из подходящих постов
6. Опубликовать в главную группу региона
"""
import asyncio
import logging
from typing import Dict, List, Optional, Tuple, Any
from datetime import datetime, timedelta
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from database.connection import AsyncSessionLocal
from database.models import Region, Community, Post, PublishSchedule
from modules.vk_monitor.monitor import VKMonitor
from modules.ai_analyzer.analyzer import PostAnalyzer
from modules.aggregation.aggregator import NewsAggregator
from modules.publisher.publisher import ContentPublisher
from config.config_secure import VK_TOKENS
from modules.service_activity_notifier import (
    notify_post_collection_start,
    notify_post_collection_complete,
    notify_post_sorting_start,
    notify_post_sorting_complete,
    notify_digest_creation_start,
    notify_digest_creation_complete,
    notify_digest_publishing_start,
    notify_digest_publishing_complete
)

logger = logging.getLogger(__name__)


class CorrectWorkflowManager:
    """Менеджер правильного workflow системы SETKA"""
    
    def __init__(self):
        self.monitor = None
        self.analyzer = None
        self.aggregator = None
        self.publisher = None
        
    async def initialize(self):
        """Инициализация компонентов"""
        try:
            # Инициализируем VK мониторинг
            tokens = [token for token in VK_TOKENS.values()]
            self.monitor = VKMonitor(tokens)
            
            # Инициализируем AI анализатор
            self.analyzer = PostAnalyzer()
            
            # Инициализируем агрегатор
            self.aggregator = NewsAggregator()
            
            # Инициализируем публикатор
            main_token = VK_TOKENS.get("VALSTAN")
            self.publisher = ContentPublisher(vk_token=main_token)
            
            logger.info("Correct workflow manager initialized")
            return True
            
        except Exception as e:
            logger.error(f"Error initializing workflow manager: {e}")
            return False
    
    async def get_current_topic_for_region(self, region_id: int, current_time: datetime) -> Optional[str]:
        """
        Получить текущую тематику для региона по расписанию
        
        Args:
            region_id: ID региона
            current_time: Текущее время
            
        Returns:
            Тематика или None если нет расписания
        """
        async with AsyncSessionLocal() as session:
            # Получаем расписание для региона на текущее время
            result = await session.execute(
                select(PublishSchedule).where(
                    and_(
                        PublishSchedule.region_id == region_id,
                        PublishSchedule.is_active == True,
                        PublishSchedule.hour == current_time.hour,
                        PublishSchedule.minute <= current_time.minute
                    )
                ).order_by(PublishSchedule.minute.desc())
            )
            
            schedule = result.scalar_one_or_none()
            
            if schedule:
                logger.info(f"Found schedule for region {region_id}: {schedule.category} at {schedule.hour}:{schedule.minute}")
                return schedule.category
            
            logger.warning(f"No schedule found for region {region_id} at {current_time.hour}:{current_time.minute}")
            return None
    
    async def get_communities_by_topic(self, region_id: int, topic: str) -> List[Community]:
        """
        Получить сообщества региона по тематике
        
        Args:
            region_id: ID региона
            topic: Тематика (novost, kultura, sport, etc.)
            
        Returns:
            Список сообществ
        """
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Community).where(
                    and_(
                        Community.region_id == region_id,
                        Community.category == topic,
                        Community.is_active == True
                    )
                )
            )
            
            communities = result.scalars().all()
            logger.info(f"Found {len(communities)} communities for topic '{topic}' in region {region_id}")
            
            return communities
    
    async def collect_posts_from_communities(
        self, 
        communities: List[Community], 
        days_back: int = 7  # Увеличиваем с 3 до 7 дней
    ) -> List[Post]:
        """
        Собрать посты из сообществ за последние N дней
        
        Args:
            communities: Список сообществ
            days_back: Количество дней назад
            
        Returns:
            Список постов
        """
        if not communities:
            return []
        
        async with AsyncSessionLocal() as session:
            # Определяем дату начала (используем UTC)
            start_date = datetime.utcnow() - timedelta(days=days_back)
            logger.info(f"Collecting posts from {len(communities)} communities, days_back={days_back}, start_date={start_date}")
            
            # Получаем посты из сообществ за последние дни
            community_ids = [c.id for c in communities]
            logger.info(f"Community IDs: {community_ids}")
            
            result = await session.execute(
                select(Post).where(
                    and_(
                        Post.community_id.in_(community_ids),
                        Post.date_published >= start_date,
                        Post.status.in_(['new', 'analyzed', 'approved'])
                    )
                ).order_by(Post.date_published.desc())
            )
            
            posts = result.scalars().all()
            logger.info(f"Collected {len(posts)} posts from {len(communities)} communities")
            
            # Отладочная информация
            if not posts:
                logger.warning("No posts found, checking without date filter...")
                result_debug = await session.execute(
                    select(Post).where(
                        and_(
                            Post.community_id.in_(community_ids),
                            Post.status.in_(['new', 'analyzed', 'approved'])
                        )
                    ).order_by(Post.date_published.desc()).limit(5)
                )
                debug_posts = result_debug.scalars().all()
                logger.info(f"Found {len(debug_posts)} posts without date filter")
                for post in debug_posts:
                    logger.info(f"  Post {post.id}: {post.date_published} (status: {post.status})")
            
            return posts
    
    async def apply_filters_to_posts(self, posts: List[Post]) -> Tuple[List[Post], List[Post]]:
        """
        Применить фильтры системы к постам
        
        Args:
            posts: Список постов
            
        Returns:
            Tuple (одобренные_посты, отклоненные_посты)
        """
        if not posts:
            return [], []
        
        # Здесь должна быть логика применения фильтров
        # Пока что используем простую логику
        
        approved_posts = []
        rejected_posts = []
        
        for post in posts:
            # Простая логика фильтрации
            if post.text and len(post.text.strip()) > 10:
                # Проверяем на спам
                spam_keywords = ['реклама', 'купить', 'продать', 'скидка', 'акция']
                if not any(keyword in post.text.lower() for keyword in spam_keywords):
                    approved_posts.append(post)
                else:
                    rejected_posts.append(post)
            else:
                rejected_posts.append(post)
        
        logger.info(f"Filtered posts: {len(approved_posts)} approved, {len(rejected_posts)} rejected")
        
        return approved_posts, rejected_posts
    
    async def create_digest_from_posts(
        self, 
        posts: List[Post], 
        topic: str, 
        region_name: str
    ) -> Optional[str]:
        """
        Создать дайджест из постов
        
        Args:
            posts: Список одобренных постов
            topic: Тематика
            region_name: Название региона
            
        Returns:
            Текст дайджеста или None
        """
        if not posts:
            return None
        
        # Сортируем посты по просмотрам
        sorted_posts = sorted(posts, key=lambda p: p.views or 0, reverse=True)
        
        # Создаем дайджест БЕЗ технической информации
        digest_parts = []
        
        for i, post in enumerate(sorted_posts[:5], 1):  # Берем топ-5 постов
            if post.text:
                # Обрезаем текст до 300 символов
                text = post.text[:300] + "..." if len(post.text) > 300 else post.text
                digest_parts.append(f"{i}. {text}")
                digest_parts.append("")  # Пустая строка между постами
        
        digest_text = "\n".join(digest_parts)
        logger.info(f"Created digest: {len(digest_text)} characters")
        
        return digest_text
    
    async def publish_digest_to_main_group(
        self, 
        digest_text: str, 
        region: Region
    ) -> bool:
        """
        Опубликовать дайджест в главную группу региона
        
        Args:
            digest_text: Текст дайджеста
            region: Регион
            
        Returns:
            True если успешно опубликовано
        """
        if not region.vk_group_id:
            logger.warning(f"No VK group ID for region {region.name}")
            return False
        
        try:
            # Здесь должна быть реальная публикация в VK
            # Пока что только логируем
            
            logger.info(f"Publishing digest to VK group {region.vk_group_id}")
            logger.info(f"Digest preview: {digest_text[:100]}...")
            
            # В реальности здесь будет:
            # result = await self.publisher.publish_to_vk(
            #     group_id=region.vk_group_id,
            #     text=digest_text
            # )
            
            return True
            
        except Exception as e:
            logger.error(f"Error publishing digest: {e}")
            return False
    
    async def process_region_by_schedule(self, region_code: str) -> Dict[str, Any]:
        """
        Обработать регион по расписанию
        
        Args:
            region_code: Код региона
            
        Returns:
            Результат обработки
        """
        try:
            if not await self.initialize():
                return {'success': False, 'error': 'Failed to initialize'}
            
            async with AsyncSessionLocal() as session:
                # Получаем регион
                result = await session.execute(
                    select(Region).where(Region.code == region_code)
                )
                region = result.scalar_one_or_none()
                
                if not region:
                    return {'success': False, 'error': f'Region {region_code} not found'}
                
                logger.info(f"Processing region: {region.name}")
                
                # Получаем текущую тематику по расписанию
                current_time = datetime.now()
                topic = await self.get_current_topic_for_region(region.id, current_time)
                
                if not topic:
                    return {
                        'success': False, 
                        'error': f'No schedule found for region {region.name} at {current_time.hour}:{current_time.minute}'
                    }
                
                logger.info(f"Current topic for {region.name}: {topic}")
                
                # Получаем сообщества по тематике
                communities = await self.get_communities_by_topic(region.id, topic)
                
                if not communities:
                    return {
                        'success': False,
                        'error': f'No communities found for topic {topic} in region {region.name}'
                    }
                
                # Уведомляем о начале сбора постов
                notify_post_collection_start(
                    region.name, 
                    topic, 
                    communities_count=len(communities)
                )
                
                # Собираем посты из сообществ
                posts = await self.collect_posts_from_communities(communities, days_back=14)
                
                # Уведомляем о завершении сбора
                notify_post_collection_complete(
                    region.name,
                    topic,
                    total_posts=len(posts),
                    processing_time=1.0
                )
                
                if not posts:
                    return {
                        'success': False,
                        'error': f'No posts found in communities for topic {topic}'
                    }
                
                # Уведомляем о начале сортировки
                notify_post_sorting_start(
                    region.name,
                    topic,
                    posts_count=len(posts)
                )
                
                # Применяем фильтры
                approved_posts, rejected_posts = await self.apply_filters_to_posts(posts)
                
                # Уведомляем о завершении сортировки
                notify_post_sorting_complete(
                    region.name,
                    topic,
                    approved_posts=len(approved_posts),
                    rejected_posts=len(rejected_posts),
                    processing_time=0.5
                )
                
                if not approved_posts:
                    return {
                        'success': False,
                        'error': f'No posts passed filters for topic {topic}'
                    }
                
                # Уведомляем о начале создания дайджеста
                notify_digest_creation_start(
                    region.name,
                    topic,
                    posts_count=len(approved_posts)
                )
                
                # Создаем дайджест
                digest_text = await self.create_digest_from_posts(
                    approved_posts, topic, region.name
                )
                
                # Уведомляем о завершении создания дайджеста
                notify_digest_creation_complete(
                    region.name,
                    topic,
                    digest_length=len(digest_text),
                    processing_time=0.3
                )
                
                if not digest_text:
                    return {
                        'success': False,
                        'error': 'Failed to create digest'
                    }
                
                # Уведомляем о начале публикации
                notify_digest_publishing_start(
                    region.name,
                    topic,
                    channel="VK"
                )
                
                # Публикуем в главную группу
                published = await self.publish_digest_to_main_group(digest_text, region)
                
                # Уведомляем о завершении публикации
                notify_digest_publishing_complete(
                    region.name,
                    topic,
                    channel="VK",
                    post_url="",  # URL будет добавлен позже
                    processing_time=1.0
                )
                
                if not published:
                    return {
                        'success': False,
                        'error': 'Failed to publish digest'
                    }
                
                return {
                    'success': True,
                    'region': region.name,
                    'topic': topic,
                    'communities_count': len(communities),
                    'posts_collected': len(posts),
                    'posts_approved': len(approved_posts),
                    'posts_rejected': len(rejected_posts),
                    'digest_length': len(digest_text),
                    'published': True
                }
                
        except Exception as e:
            logger.error(f"Error processing region {region_code}: {e}")
            return {'success': False, 'error': str(e)}
    
    async def process_all_regions_by_schedule(self) -> Dict[str, Any]:
        """
        Обработать все регионы по расписанию
        
        Returns:
            Общий результат обработки
        """
        try:
            async with AsyncSessionLocal() as session:
                # Получаем все регионы с расписанием
                result = await session.execute(
                    select(Region).where(
                        and_(
                            Region.is_active == True,
                            Region.vk_group_id.isnot(None)
                        )
                    )
                )
                regions = result.scalars().all()
                
                if not regions:
                    return {'success': False, 'error': 'No active regions found'}
                
                logger.info(f"Processing {len(regions)} regions")
                
                results = {}
                total_success = 0
                total_failed = 0
                
                for region in regions:
                    logger.info(f"Processing region: {region.name}")
                    
                    result = await self.process_region_by_schedule(region.code)
                    results[region.code] = result
                    
                    if result.get('success'):
                        total_success += 1
                    else:
                        total_failed += 1
                    
                    # Пауза между регионами
                    await asyncio.sleep(2)
                
                return {
                    'success': True,
                    'total_regions': len(regions),
                    'successful': total_success,
                    'failed': total_failed,
                    'results': results
                }
                
        except Exception as e:
            logger.error(f"Error processing all regions: {e}")
            return {'success': False, 'error': str(e)}


# Глобальный экземпляр
correct_workflow_manager = CorrectWorkflowManager()
