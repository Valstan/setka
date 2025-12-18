"""
Структурные фильтры - быстрая отсечка
Уровень 1 из Postopus: мгновенные проверки
"""
import logging
from datetime import datetime, timedelta
from typing import Any, List, Set
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .base import FastFilter, DBFilter, FilterResult
from database.models import Post, Filter

logger = logging.getLogger(__name__)


class StructuralDuplicateFilter(DBFilter):
    """
    Проверка структурных дубликатов через LIP (owner_id_post_id)
    
    Из Postopus CORE_CONCEPTS:
    "lip = f"{owner_id}_{post_id}" - простейший уникальный идентификатор"
    "Мгновенная проверка (O(1) в set), 100% надежность"
    """
    
    def __init__(self):
        super().__init__(name="Structural Duplicate Check", priority=10)
    
    async def apply(self, post: Any, context: dict) -> FilterResult:
        """Проверка на структурный дубликат"""
        session = context.get('session')
        
        if not session:
            # Если нет сессии БД - пропускаем проверку
            return FilterResult(passed=True)
        
        # Получить LIP (отпечаток)
        if hasattr(post, 'fingerprint_lip'):
            lip = post.fingerprint_lip
        elif hasattr(post, 'vk_owner_id') and hasattr(post, 'vk_post_id'):
            lip = f"{post.vk_owner_id}_{post.vk_post_id}"
        else:
            return FilterResult(passed=True)
        
        # Проверить существование в БД
        result = await session.execute(
            select(Post).where(Post.fingerprint_lip == lip)
        )
        existing_posts = result.scalars().all()
        
        # Если найдено больше одного поста с таким lip (кроме текущего)
        duplicates = [p for p in existing_posts if p.id != getattr(post, 'id', None)]
        
        if duplicates:
            return FilterResult(
                passed=False,
                reason=f"Structural duplicate: LIP {lip} already exists ({len(duplicates)} duplicates)"
            )
        
        return FilterResult(passed=True)


class DateFilter(FastFilter):
    """
    Фильтр по дате публикации
    
    Из Postopus: Старые посты (> 72 часов) не публикуются
    """
    
    def __init__(self, max_age_hours: int = 72):
        super().__init__(name="Date Filter", priority=11)
        self.max_age_hours = max_age_hours
    
    async def apply(self, post: Any, context: dict) -> FilterResult:
        """Проверка свежести поста"""
        if not hasattr(post, 'date_published') or not post.date_published:
            # Если нет даты - пропускаем (возможно новый пост)
            return FilterResult(passed=True)
        
        # Рассчитать возраст
        age = datetime.utcnow() - post.date_published
        age_hours = age.total_seconds() / 3600
        
        if age_hours > self.max_age_hours:
            return FilterResult(
                passed=False,
                reason=f"Too old: {age_hours:.1f} hours (max {self.max_age_hours})"
            )
        
        # Бонус за свежесть
        if age_hours < 6:
            score_modifier = 10
        elif age_hours < 24:
            score_modifier = 5
        else:
            score_modifier = 0
        
        return FilterResult(
            passed=True,
            score_modifier=score_modifier,
            metadata={'age_hours': age_hours}
        )


class BlacklistIDFilter(DBFilter):
    """
    Фильтр по черному списку ID пользователей/групп
    
    Из Postopus: black_id - список ID для блокировки
    """
    
    def __init__(self):
        super().__init__(name="Blacklist ID Check", priority=12)
        self._black_ids_cache = None
        self._cache_time = None
        self._cache_ttl = 300  # 5 минут
    
    async def apply(self, post: Any, context: dict) -> FilterResult:
        """Проверка ID на черном списке"""
        session = context.get('session')
        
        if not session:
            return FilterResult(passed=True)
        
        # Получить черный список (с кэшированием)
        black_ids = await self._get_black_ids(session)
        
        # Проверить owner_id
        if hasattr(post, 'vk_owner_id'):
            owner_id = abs(post.vk_owner_id)
            
            if owner_id in black_ids:
                return FilterResult(
                    passed=False,
                    reason=f"Blacklisted owner ID: {owner_id}"
                )
        
        # Проверить from_id (автор поста, если отличается от владельца)
        if hasattr(post, 'vk_from_id') and post.vk_from_id:
            from_id = abs(post.vk_from_id)
            
            if from_id in black_ids:
                return FilterResult(
                    passed=False,
                    reason=f"Blacklisted author ID: {from_id}"
                )
        
        return FilterResult(passed=True)
    
    async def _get_black_ids(self, session: AsyncSession) -> set:
        """Получить черный список ID с кэшированием"""
        now = datetime.utcnow()
        
        # Проверить кэш
        if (self._black_ids_cache is not None and 
            self._cache_time is not None and
            (now - self._cache_time).total_seconds() < self._cache_ttl):
            return self._black_ids_cache
        
        # Загрузить из БД
        result = await session.execute(
            select(Filter.pattern).where(
                Filter.type == 'black_id',
                Filter.is_active == True
            )
        )
        
        patterns = result.scalars().all()
        black_ids = set()
        
        for pattern in patterns:
            try:
                black_ids.add(int(pattern))
            except (ValueError, TypeError):
                logger.warning(f"Invalid black_id pattern: {pattern}")
        
        # Обновить кэш
        self._black_ids_cache = black_ids
        self._cache_time = now
        
        logger.debug(f"Loaded {len(black_ids)} blacklisted IDs")
        
        return black_ids


class OnlyMainNewsFilter(FastFilter):
    """
    Фильтр "только от администраторов группы"
    
    Из Postopus LESSONS_LEARNED:
    "Если owner_id совпадает с from_id → админ группы"
    "Если различаются → пользователь написал"
    """
    
    def __init__(self, strict_groups: List[int] = None):
        super().__init__(name="Only Main News Check", priority=13)
        self.strict_groups = set(strict_groups) if strict_groups else set()
    
    async def apply(self, post: Any, context: dict) -> FilterResult:
        """Проверка что пост от админа группы"""
        if not hasattr(post, 'vk_owner_id') or not hasattr(post, 'vk_from_id'):
            return FilterResult(passed=True)
        
        owner_id = abs(post.vk_owner_id)
        from_id = abs(post.vk_from_id) if post.vk_from_id else owner_id
        
        # Если группа в списке "только от админов"
        if owner_id in self.strict_groups:
            # Проверить что автор = владелец (админ группы)
            if owner_id != from_id:
                return FilterResult(
                    passed=False,
                    reason=f"Not from group admin (owner: {owner_id}, author: {from_id})"
                )
        
        return FilterResult(passed=True)

