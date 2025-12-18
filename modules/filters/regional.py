"""
Региональные фильтры
Проверка релевантности контента для региона
"""
import logging
from typing import Any, List, Set
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .base import FastFilter, DBFilter, FilterResult
from database.models import Region

logger = logging.getLogger(__name__)


class RegionalRelevanceFilter(DBFilter):
    """
    Фильтр релевантности для региона
    
    Из Postopus CORE_CONCEPTS:
    "Один и тот же контент может быть релевантен для одного региона 
     и бесполезен для другого"
    
    "Региональность - это контекст, а не фильтр"
    """
    
    def __init__(self, required_matches: int = 1):
        super().__init__(name="Regional Relevance Check", priority=60)
        self.required_matches = required_matches
        self._keywords_cache = {}  # region_id -> keywords
        self._cache_time = None
    
    async def apply(self, post: Any, context: dict) -> FilterResult:
        """Проверка релевантности для региона"""
        session = context.get('session')
        region_id = context.get('region_id')
        
        # Если нет контекста региона - пропускаем проверку
        if not session or not region_id:
            return FilterResult(passed=True)
        
        if not hasattr(post, 'text') or not post.text:
            return FilterResult(passed=True)
        
        # Получить ключевые слова региона
        keywords = await self._get_region_keywords(session, region_id)
        
        if not keywords:
            # Нет настроенных ключевых слов - пропускаем
            return FilterResult(passed=True)
        
        # Подсчитать совпадения
        text_lower = post.text.lower()
        matches = []
        
        for keyword in keywords:
            if keyword.lower() in text_lower:
                matches.append(keyword)
        
        if len(matches) >= self.required_matches:
            # Бонус за высокую релевантность
            score_modifier = min(len(matches) * 5, 20)
            
            return FilterResult(
                passed=True,
                score_modifier=score_modifier,
                metadata={'regional_matches': matches}
            )
        else:
            return FilterResult(
                passed=False,
                reason=f"Not regionally relevant (found {len(matches)} matches, need {self.required_matches})",
                metadata={'matches': matches}
            )
    
    async def _get_region_keywords(self, session: AsyncSession, region_id: int) -> Set[str]:
        """
        Получить ключевые слова региона
        
        TODO: Расширить для получения из БД
        Сейчас - заглушка
        """
        # Получить регион
        result = await session.execute(
            select(Region).where(Region.id == region_id)
        )
        region = result.scalar_one_or_none()
        
        if not region:
            return set()
        
        # Базовые ключевые слова из названия
        keywords = set()
        
        # Парсим название региона
        name_parts = region.name.upper().replace(' - ИНФО', '').replace('-', ' ').split()
        keywords.update(name_parts)
        
        # Добавляем код
        keywords.add(region.code.upper())
        
        # TODO: Загружать из БД (таблица regional_keywords)
        # TODO: Добавлять населенные пункты района
        # TODO: Морфологические варианты (Малмыж, Малмыжский, Малмыжского)
        
        return keywords


class NeighborRegionFilter(FastFilter):
    """
    Фильтр для постов из соседних регионов
    
    Из Postopus: sosed - посты соседей, но только с хештегом #Новости
    """
    
    def __init__(self, require_hashtag: bool = True):
        super().__init__(name="Neighbor Region Check", priority=61)
        self.require_hashtag = require_hashtag
    
    async def apply(self, post: Any, context: dict) -> FilterResult:
        """Проверка постов из соседних регионов"""
        is_neighbor = context.get('is_neighbor_region', False)
        
        if not is_neighbor:
            # Не из соседнего региона - пропускаем проверку
            return FilterResult(passed=True)
        
        if not self.require_hashtag:
            return FilterResult(passed=True)
        
        # Для соседей требуем хештег #Новости
        if not hasattr(post, 'text') or not post.text:
            return FilterResult(
                passed=False,
                reason="Neighbor region post without text"
            )
        
        text_lower = post.text.lower()
        news_hashtags = ['#новости', '#news', 'новости']
        
        has_hashtag = any(tag in text_lower for tag in news_hashtags)
        
        if not has_hashtag:
            return FilterResult(
                passed=False,
                reason="Neighbor region post without #Новости hashtag"
            )
        
        return FilterResult(passed=True, score_modifier=5)

