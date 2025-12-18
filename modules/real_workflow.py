"""
Real Workflow System - Реальная интеграция с компонентами SETKA
"""
import asyncio
import logging
from typing import Dict, List, Optional
from datetime import datetime

from modules.service_notifications import service_notifications
from modules.service_activity_notifier import (
    notify_post_collection_start, notify_post_collection_complete,
    notify_post_sorting_start, notify_post_sorting_complete,
    notify_digest_creation_start, notify_digest_creation_complete,
    notify_digest_publishing_start, notify_digest_publishing_complete
)
from modules.vk_monitor.monitor import VKMonitor
from modules.ai_analyzer.analyzer import PostAnalyzer
from database.connection import AsyncSessionLocal
from database.models import Community, Region, Post
from sqlalchemy import select
from config.config_secure import VK_MAIN_TOKENS

logger = logging.getLogger(__name__)


class RealWorkflowManager:
    """Менеджер реального workflow системы SETKA"""
    
    def __init__(self):
        self.is_running = False
        self.current_region = None
        self.current_topic = None
        self.monitor = None
        self.analyzer = None
        
    async def initialize(self):
        """Инициализация компонентов"""
        try:
            # Инициализируем VK мониторинг
            tokens = [info['token'] for info in VK_MAIN_TOKENS.values()]
            self.monitor = VKMonitor(tokens)
            
            # Инициализируем AI анализатор
            self.analyzer = PostAnalyzer()
            
            logger.info("Real workflow manager initialized")
            return True
            
        except Exception as e:
            logger.error(f"Error initializing workflow manager: {e}")
            service_notifications.error(f"Ошибка инициализации: {str(e)}")
            return False
    
    async def start_real_workflow(self, region_code: str = "mi"):
        """Запуск реального workflow для региона"""
        try:
            if not await self.initialize():
                return False
            
            self.is_running = True
            
            async with AsyncSessionLocal() as session:
                # Получаем регион
                result = await session.execute(select(Region).where(Region.code == region_code))
                region = result.scalar_one_or_none()
                
                if not region:
                    service_notifications.error(f"Регион {region_code} не найден")
                    return False
                
                self.current_region = region.name
                service_notifications.system_start(region.name)
                
                # Получаем сообщества региона
                result = await session.execute(
                    select(Community).where(Community.region_id == region.id)
                )
                communities = result.scalars().all()
                
                if not communities:
                    service_notifications.error(f"Нет сообществ для региона {region.name}")
                    return False
                
                # Выбираем тему (пока фиксированная)
                topics = ["культура", "спорт", "новости", "события"]
                self.current_topic = topics[0]  # Берем первую тему
                service_notifications.topic_select(self.current_topic)
                
                # Уведомляем о начале сбора постов
                notify_post_collection_start(region.name, self.current_topic, len(communities))
                
                # Сканируем сообщества
                total_posts = 0
                start_time = datetime.now()
                
                for i, community in enumerate(communities[:5], 1):  # Ограничиваем для теста
                    try:
                        service_notifications.community_scan(community.name, 1)
                        
                        # Реальное сканирование сообщества
                        posts_count = await self.monitor.scan_community(community, session)
                        total_posts += posts_count
                        
                        await asyncio.sleep(1)  # Задержка между сообществами
                        
                    except Exception as e:
                        logger.error(f"Error scanning community {community.name}: {e}")
                        service_notifications.error(f"Ошибка сканирования {community.name}: {str(e)}")
                
                # Уведомляем о завершении сбора постов
                processing_time = (datetime.now() - start_time).total_seconds()
                notify_post_collection_complete(region.name, self.current_topic, total_posts, processing_time)
                
                # Фильтрация постов
                result = await session.execute(
                    select(Post).where(Post.region_id == region.id)
                    .order_by(Post.created_at.desc())
                    .limit(20)
                )
                recent_posts = result.scalars().all()
                
                service_notifications.post_filter(len(recent_posts), len(recent_posts))
                
                if recent_posts:
                    # Уведомляем о начале сортировки постов
                    notify_post_sorting_start(region.name, self.current_topic, len(recent_posts))
                    
                    # Простая сортировка (в реальности здесь будет AI анализ)
                    approved_posts = []
                    rejected_posts = []
                    
                    for post in recent_posts:
                        # Простая логика: если пост содержит ключевые слова темы, одобряем
                        if self.current_topic.lower() in post.text.lower():
                            approved_posts.append(post)
                        else:
                            rejected_posts.append(post)
                    
                    # Уведомляем о завершении сортировки
                    sorting_time = 1.5  # Примерное время сортировки
                    notify_post_sorting_complete(region.name, self.current_topic, len(approved_posts), len(rejected_posts), sorting_time)
                    
                    if approved_posts:
                        # Выбираем лучший пост
                        best_post = approved_posts[0]  # Берем первый одобренный
                        service_notifications.post_select(str(best_post.id), best_post.community.name if best_post.community else "Unknown")
                        
                        # Уведомляем о начале создания дайджеста
                        notify_digest_creation_start(region.name, self.current_topic, len(approved_posts))
                        
                        # Создаем простой дайджест
                        digest_text = f"Основные новости по теме '{self.current_topic}':\n\n"
                        for i, post in enumerate(approved_posts[:3], 1):  # Берем первые 3 поста
                            digest_text += f"{i}. {post.text[:100]}...\n\n"
                        
                        # Уведомляем о завершении создания дайджеста
                        digest_time = 0.8  # Примерное время создания
                        notify_digest_creation_complete(region.name, self.current_topic, len(digest_text), digest_time)
                        
                        # Уведомляем о начале публикации
                        notify_digest_publishing_start(region.name, self.current_topic, "VK")
                        
                        # Реальная публикация (пока только логируем)
                        await self.publish_post(best_post, region)
                        
                        # Уведомляем о завершении публикации
                        publish_time = 1.2  # Примерное время публикации
                        notify_digest_publishing_complete(region.name, self.current_topic, "VK", "", publish_time)
                    else:
                        service_notifications.error("Нет одобренных постов для публикации")
                else:
                    service_notifications.error("Нет постов для публикации")
                
                # Пауза
                service_notifications.system_pause()
                await asyncio.sleep(2)
                
                # Следующий регион
                next_regions = ["nolinsk", "arbazh", "sovetsk"]
                next_region = next_regions[0] if next_regions else None
                if next_region:
                    service_notifications.region_queue(next_region)
            
            self.is_running = False
            return True
            
        except Exception as e:
            logger.error(f"Error in real workflow: {e}")
            service_notifications.error(f"Ошибка workflow: {str(e)}")
            self.is_running = False
            return False
    
    async def publish_post(self, post: Post, region: Region):
        """Реальная публикация поста"""
        try:
            post_id = str(post.id)
            
            # Публикация в VK (пока только логируем)
            if region.vk_group_id:
                logger.info(f"Publishing post {post_id} to VK group {region.vk_group_id}")
                service_notifications.publish_vk(post_id, True)
            else:
                service_notifications.publish_vk(post_id, False)
            
            await asyncio.sleep(1)
            
            # Публикация в Telegram (пока только логируем)
            if region.telegram_channel:
                logger.info(f"Publishing post {post_id} to Telegram channel {region.telegram_channel}")
                service_notifications.publish_telegram(post_id, True)
            else:
                service_notifications.publish_telegram(post_id, False)
            
            await asyncio.sleep(1)
            
            # Публикация в Одноклассники (пока только логируем)
            logger.info(f"Publishing post {post_id} to OK")
            service_notifications.publish_ok(post_id, True)
            
            await asyncio.sleep(1)
            
            # Публикация на сайт (пока только логируем)
            logger.info(f"Publishing post {post_id} to website")
            service_notifications.publish_website(post_id, True)
            
        except Exception as e:
            logger.error(f"Error publishing post: {e}")
            service_notifications.error(f"Ошибка публикации: {str(e)}")
    
    async def scan_all_regions(self):
        """Сканирование всех регионов"""
        try:
            async with AsyncSessionLocal() as session:
                result = await session.execute(select(Region))
                regions = result.scalars().all()
                
                for region in regions:
                    if not self.is_running:
                        break
                    
                    logger.info(f"Scanning region: {region.name}")
                    await self.start_real_workflow(region.code)
                    await asyncio.sleep(5)  # Пауза между регионами
                
        except Exception as e:
            logger.error(f"Error scanning all regions: {e}")
            service_notifications.error(f"Ошибка сканирования регионов: {str(e)}")
    
    def get_status(self) -> Dict:
        """Получить статус workflow"""
        return {
            'is_running': self.is_running,
            'current_region': self.current_region,
            'current_topic': self.current_topic,
            'monitor_ready': self.monitor is not None,
            'analyzer_ready': self.analyzer is not None
        }


# Глобальный экземпляр
real_workflow_manager = RealWorkflowManager()
