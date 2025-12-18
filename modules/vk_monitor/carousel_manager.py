"""
VK Carousel System - Карусельная система опроса регионов
Оптимизирует нагрузку на VK API через поочередный опрос регионов
"""
import asyncio
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update

from database.connection import get_db_session
from database.models import Region, Community, Post
from config.config_secure import VK_TOKENS, VK_TOKEN_CONFIG
from modules.vk_monitor.vk_client import VKClient

logger = logging.getLogger(__name__)


class ScanStatus(Enum):
    """Статус сканирования региона"""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class RegionScanTask:
    """Задача сканирования региона"""
    region_id: int
    region_code: str
    region_name: str
    communities: List[int]
    token_name: str
    priority: int = 50
    status: ScanStatus = ScanStatus.PENDING
    scheduled_time: Optional[datetime] = None
    started_time: Optional[datetime] = None
    completed_time: Optional[datetime] = None
    error_message: Optional[str] = None
    posts_found: int = 0


class VKCarouselManager:
    """Менеджер карусельной системы опроса VK"""
    
    def __init__(self):
        self.scan_interval_minutes = 60  # Интервал между опросами региона
        self.max_concurrent_scans = 1    # Максимум одновременных сканирований
        self.request_delay_seconds = 3  # Задержка между запросами к VK API
        self.active_scans: Dict[int, RegionScanTask] = {}
        self.scan_history: List[RegionScanTask] = []
        self.token_rotation_index = 0
        
    async def get_next_region_to_scan(self, db: AsyncSession) -> Optional[RegionScanTask]:
        """Получить следующий регион для сканирования"""
        try:
            # Получить все активные регионы
            regions_result = await db.execute(
                select(Region).where(Region.is_active == True).order_by(Region.priority.desc())
            )
            regions = regions_result.scalars().all()
            
            if not regions:
                logger.warning("No active regions found for scanning")
                return None
            
            # Найти регион, который давно не сканировался
            current_time = datetime.now()
            oldest_scan_time = None
            oldest_region = None
            
            for region in regions:
                # Получить время последнего сканирования региона
                last_scan_result = await db.execute(
                    select(Post.created_at)
                    .join(Community)
                    .where(Community.region_id == region.id)
                    .order_by(Post.created_at.desc())
                    .limit(1)
                )
                last_scan = last_scan_result.scalar_one_or_none()
                
                if not last_scan:
                    # Регион никогда не сканировался
                    oldest_region = region
                    break
                
                if not oldest_scan_time or last_scan < oldest_scan_time:
                    oldest_scan_time = last_scan
                    oldest_region = region
            
            if not oldest_region:
                return None
            
            # Получить сообщества региона
            communities_result = await db.execute(
                select(Community.id).where(
                    Community.region_id == oldest_region.id,
                    Community.is_active == True
                )
            )
            community_ids = [row[0] for row in communities_result.fetchall()]
            
            if not community_ids:
                logger.warning(f"No active communities found for region {oldest_region.code}")
                return None
            
            # Выбрать токен для сканирования
            token_name = self._get_next_token()
            
            # Создать задачу сканирования
            task = RegionScanTask(
                region_id=oldest_region.id,
                region_code=oldest_region.code,
                region_name=oldest_region.name,
                communities=community_ids,
                token_name=token_name,
                priority=oldest_region.priority or 50,
                scheduled_time=current_time
            )
            
            logger.info(f"Created scan task for region {oldest_region.code} with {len(community_ids)} communities")
            return task
            
        except Exception as e:
            logger.error(f"Error getting next region to scan: {e}")
            return None
    
    def _get_next_token(self) -> str:
        """Получить следующий токен для использования (ротация)"""
        available_tokens = [name for name, token in VK_TOKENS.items() if token]
        
        if not available_tokens:
            logger.error("No available VK tokens found")
            return "VALSTAN"  # Fallback
        
        # Ротация токенов для равномерного распределения нагрузки
        token_name = available_tokens[self.token_rotation_index % len(available_tokens)]
        self.token_rotation_index += 1
        
        logger.debug(f"Selected token {token_name} for scanning")
        return token_name
    
    async def execute_region_scan(self, task: RegionScanTask, db: AsyncSession) -> bool:
        """Выполнить сканирование региона"""
        try:
            task.status = ScanStatus.IN_PROGRESS
            task.started_time = datetime.now()
            self.active_scans[task.region_id] = task
            
            logger.info(f"Starting scan for region {task.region_code} using token {task.token_name}")
            
            # Получить токен
            token = VK_TOKENS.get(task.token_name)
            if not token:
                raise ValueError(f"Token {task.token_name} not found")
            
            # Создать VK клиент
            vk_client = VKClient(token)
            
            # Получить сообщества региона
            communities_result = await db.execute(
                select(Community).where(Community.id.in_(task.communities))
            )
            communities = communities_result.scalars().all()
            
            total_posts = 0
            
            # Сканировать каждое сообщество
            for community in communities:
                try:
                    logger.debug(f"Scanning community {community.vk_id} ({community.name})")
                    
                    # Получить посты из сообщества
                    posts_data = await vk_client.get_posts(
                        owner_id=community.vk_id,
                        count=50,  # Ограничиваем количество для оптимизации
                        extended=1
                    )
                    
                    if posts_data and 'items' in posts_data:
                        posts_count = len(posts_data['items'])
                        total_posts += posts_count
                        
                        logger.debug(f"Found {posts_count} posts in community {community.vk_id}")
                        
                        # Сохранить посты в БД (упрощенная версия)
                        await self._save_posts_to_db(posts_data['items'], community, db)
                    
                    # Задержка между запросами к разным сообществам
                    await asyncio.sleep(self.request_delay_seconds)
                    
                except Exception as e:
                    logger.error(f"Error scanning community {community.vk_id}: {e}")
                    continue
            
            # Завершить задачу
            task.status = ScanStatus.COMPLETED
            task.completed_time = datetime.now()
            task.posts_found = total_posts
            
            logger.info(f"Completed scan for region {task.region_code}: {total_posts} posts found")
            
            # Добавить в историю
            self.scan_history.append(task)
            
            # Ограничить размер истории
            if len(self.scan_history) > 100:
                self.scan_history = self.scan_history[-100:]
            
            return True
            
        except Exception as e:
            logger.error(f"Error executing scan for region {task.region_code}: {e}")
            task.status = ScanStatus.FAILED
            task.error_message = str(e)
            task.completed_time = datetime.now()
            return False
            
        finally:
            # Удалить из активных сканирований
            if task.region_id in self.active_scans:
                del self.active_scans[task.region_id]
    
    async def _save_posts_to_db(self, posts_data: List[Dict], community: Community, db: AsyncSession):
        """Сохранить посты в базу данных"""
        try:
            for post_data in posts_data:
                # Проверить, существует ли уже пост
                existing_post = await db.execute(
                    select(Post).where(
                        Post.vk_post_id == post_data['id'],
                        Post.vk_owner_id == post_data['owner_id']
                    )
                )
                
                if existing_post.scalar_one_or_none():
                    continue  # Пост уже существует
                
                # Создать новый пост
                new_post = Post(
                    region_id=community.region_id,
                    community_id=community.id,
                    vk_post_id=post_data['id'],
                    vk_owner_id=post_data['owner_id'],
                    text=post_data.get('text', ''),
                    date_published=datetime.fromtimestamp(post_data['date']),
                    views=post_data.get('views', {}).get('count', 0),
                    likes=post_data.get('likes', {}).get('count', 0),
                    reposts=post_data.get('reposts', {}).get('count', 0),
                    comments=post_data.get('comments', {}).get('count', 0),
                    status='new'
                )
                
                db.add(new_post)
            
            await db.commit()
            logger.debug(f"Saved posts for community {community.vk_id}")
            
        except Exception as e:
            logger.error(f"Error saving posts for community {community.vk_id}: {e}")
            await db.rollback()
    
    async def get_carousel_status(self, db: AsyncSession) -> Dict:
        """Получить статус карусели"""
        try:
            # Получить все регионы
            regions_result = await db.execute(
                select(Region.code, Region.name).where(Region.is_active == True)
            )
            regions = regions_result.fetchall()
            
            # Получить последнее сканирование
            last_scan_result = await db.execute(
                select(Post.created_at, Region.code)
                .join(Community)
                .join(Region)
                .order_by(Post.created_at.desc())
                .limit(1)
            )
            last_scan = last_scan_result.first()
            
            # Определить следующий регион
            region_codes = [r.code for r in regions]
            if last_scan and last_scan.code in region_codes:
                current_index = region_codes.index(last_scan.code)
                next_index = (current_index + 1) % len(region_codes)
                next_region = region_codes[next_index]
            else:
                next_region = region_codes[0] if region_codes else None
            
            # Рассчитать время следующего сканирования
            if last_scan:
                next_scan_time = last_scan.created_at + timedelta(minutes=self.scan_interval_minutes)
            else:
                next_scan_time = datetime.now()
            
            return {
                "current_region": last_scan.code if last_scan else None,
                "next_region": next_region,
                "last_processed": last_scan.created_at if last_scan else None,
                "next_scan_time": next_scan_time,
                "regions_queue": region_codes,
                "scan_interval_minutes": self.scan_interval_minutes,
                "active_scans": len(self.active_scans),
                "total_scans_today": len([t for t in self.scan_history 
                                        if t.completed_time and t.completed_time.date() == datetime.now().date()])
            }
            
        except Exception as e:
            logger.error(f"Error getting carousel status: {e}")
            return {
                "current_region": None,
                "next_region": None,
                "last_processed": None,
                "next_scan_time": None,
                "regions_queue": [],
                "scan_interval_minutes": self.scan_interval_minutes,
                "active_scans": 0,
                "total_scans_today": 0
            }
    
    async def optimize_scan_frequency(self, db: AsyncSession) -> Dict:
        """Оптимизировать частоту сканирования на основе нагрузки"""
        try:
            # Анализировать текущую нагрузку
            recent_scans = [t for t in self.scan_history 
                          if t.completed_time and t.completed_time >= datetime.now() - timedelta(hours=24)]
            
            if len(recent_scans) > 20:
                # Высокая нагрузка - увеличить интервал
                self.scan_interval_minutes = min(self.scan_interval_minutes + 15, 120)
                logger.info(f"Increased scan interval to {self.scan_interval_minutes} minutes due to high load")
            elif len(recent_scans) < 5:
                # Низкая нагрузка - уменьшить интервал
                self.scan_interval_minutes = max(self.scan_interval_minutes - 15, 30)
                logger.info(f"Decreased scan interval to {self.scan_interval_minutes} minutes due to low load")
            
            return {
                "message": "Scan frequency optimization completed",
                "recommended_interval_minutes": self.scan_interval_minutes,
                "strategy": "carousel",
                "next_optimization": datetime.now() + timedelta(hours=24),
                "current_load": "high" if len(recent_scans) > 20 else "low" if len(recent_scans) < 5 else "medium"
            }
            
        except Exception as e:
            logger.error(f"Error optimizing scan frequency: {e}")
            return {
                "message": "Error during optimization",
                "recommended_interval_minutes": self.scan_interval_minutes,
                "strategy": "carousel",
                "next_optimization": datetime.now() + timedelta(hours=1),
                "current_load": "unknown"
            }


# Глобальный экземпляр менеджера карусели
carousel_manager = VKCarouselManager()
