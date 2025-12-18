"""
Filter Pipeline - конвейер фильтрации постов

Концепция из Postopus:
"Данные проходят через цепочку фильтров, каждый может отбросить элемент"

Порядок важен:
1. Быстрые проверки (отпечатки, даты) - O(1)
2. Средние (текстовый анализ) - O(n)
3. Дорогие (API запросы, AI) - O(n * k)
"""
import logging
from typing import List, Dict, Any
from dataclasses import dataclass

from .base import BaseFilter, FilterResult

logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    """Результат работы пайплайна"""
    original_count: int  # Входное количество
    passed_count: int  # Прошедших все фильтры
    filtered_count: int  # Отфильтрованных
    filter_stats: List[Dict]  # Статистика по каждому фильтру
    processing_time: float  # Время обработки (секунды)


class FilterPipeline:
    """
    Pipeline фильтрации постов
    
    Пример использования:
    ```python
    pipeline = FilterPipeline([
        StructuralDuplicateFilter(),
        DateFilter(max_age_hours=72),
        BlacklistWordFilter(),
        RegionalRelevanceFilter(),
        AIClassificationFilter()  # Дорогой, но точный
    ])
    
    results = await pipeline.process(posts, context)
    ```
    """
    
    def __init__(self, filters: List[BaseFilter]):
        """
        Args:
            filters: Список фильтров для применения
        """
        # Сортируем фильтры по приоритету (меньше = раньше)
        self.filters = sorted(filters, key=lambda f: f.priority)
        logger.info(f"Pipeline initialized with {len(self.filters)} filters")
        for f in self.filters:
            logger.info(f"  - {f.name} (priority: {f.priority})")
    
    async def process(
        self,
        posts: List[Any],
        context: dict = None
    ) -> tuple[List[Any], PipelineResult]:
        """
        Обработать список постов через все фильтры
        
        Args:
            posts: Список постов для фильтрации
            context: Контекст обработки
            
        Returns:
            (passed_posts, pipeline_result)
        """
        if context is None:
            context = {}
        
        import time
        start_time = time.time()
        
        original_count = len(posts)
        remaining_posts = posts.copy()
        
        logger.info(f"Starting pipeline with {original_count} posts")
        
        # Применяем фильтры последовательно
        for filter_obj in self.filters:
            if not remaining_posts:
                logger.info(f"No posts remaining, stopping pipeline at {filter_obj.name}")
                break
            
            logger.debug(f"Applying filter: {filter_obj.name} ({len(remaining_posts)} posts)")
            
            # Применить фильтр ко всем оставшимся постам
            passed_posts = []
            
            for post in remaining_posts:
                try:
                    result = await filter_obj.apply(post, context)
                    filter_obj.update_stats(result)
                    
                    if result.passed:
                        # Применить модификатор оценки если есть
                        if hasattr(post, 'ai_score') and result.score_modifier != 0:
                            post.ai_score = (post.ai_score or 50) + result.score_modifier
                        
                        passed_posts.append(post)
                    else:
                        # Пост отфильтрован
                        logger.debug(f"Post filtered by {filter_obj.name}: {result.reason}")
                        
                        # Помечаем пост как отфильтрованный
                        if hasattr(post, 'status'):
                            post.status = 'rejected'
                        if hasattr(post, 'is_spam') and 'spam' in result.reason.lower():
                            post.is_spam = True
                
                except Exception as e:
                    logger.error(f"Error in filter {filter_obj.name}: {e}")
                    # В случае ошибки - пропускаем пост дальше
                    passed_posts.append(post)
            
            filtered_count = len(remaining_posts) - len(passed_posts)
            logger.info(f"  {filter_obj.name}: {filtered_count} filtered, {len(passed_posts)} passed")
            
            remaining_posts = passed_posts
        
        processing_time = time.time() - start_time
        passed_count = len(remaining_posts)
        filtered_count = original_count - passed_count
        
        # Собрать статистику
        filter_stats = [f.get_stats() for f in self.filters]
        
        result = PipelineResult(
            original_count=original_count,
            passed_count=passed_count,
            filtered_count=filtered_count,
            filter_stats=filter_stats,
            processing_time=processing_time
        )
        
        logger.info(
            f"Pipeline complete: {original_count} → {passed_count} "
            f"({filtered_count} filtered, {processing_time:.2f}s)"
        )
        
        return remaining_posts, result
    
    def get_statistics(self) -> Dict[str, Any]:
        """Получить детальную статистику пайплайна"""
        return {
            'filters': [f.get_stats() for f in self.filters],
            'total_filters': len(self.filters)
        }
    
    def reset_statistics(self):
        """Сбросить статистику всех фильтров"""
        for f in self.filters:
            f.reset_stats()

