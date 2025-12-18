"""
Фильтры качества контента
"""
import logging
import re
from typing import Any, List

from .base import FastFilter, FilterResult

logger = logging.getLogger(__name__)


class TextQualityFilter(FastFilter):
    """
    Проверка качества текста
    
    Критерии:
    - Минимум осмысленных слов
    - Не слишком много эмодзи
    - Читабельность
    """
    
    def __init__(self, min_words: int = 3):
        super().__init__(name="Text Quality Check", priority=70)
        self.min_words = min_words
    
    async def apply(self, post: Any, context: dict) -> FilterResult:
        """Проверка качества текста"""
        if not hasattr(post, 'text') or not post.text:
            # Без текста - проверим медиа
            if hasattr(post, 'attachments') and post.attachments:
                return FilterResult(passed=True)
            return FilterResult(passed=False, reason="No content")
        
        text = post.text
        
        # Подсчет слов (кириллица и латиница)
        words = re.findall(r'[а-яёА-ЯЁa-zA-Z]{2,}', text)
        word_count = len(words)
        
        if word_count < self.min_words:
            return FilterResult(
                passed=False,
                reason=f"Too few words: {word_count} (min {self.min_words})"
            )
        
        # Проверка на избыток эмодзи
        emoji_count = len(re.findall(r'[😀-🙏🌀-🗿🚀-🛿]', text))
        if emoji_count > len(text) * 0.3:  # Более 30% текста - эмодзи
            return FilterResult(
                passed=False,
                reason=f"Too many emojis: {emoji_count}",
                metadata={'emoji_ratio': emoji_count / len(text)}
            )
        
        # Проверка на читабельность (нет избытка знаков препинания)
        punctuation_count = len(re.findall(r'[!?]{3,}', text))
        if punctuation_count > 5:
            return FilterResult(
                passed=False,
                reason="Too much punctuation (spam-like)",
                score_modifier=-10
            )
        
        # Бонус за хороший текст
        if word_count > 20:
            score_modifier = 5
        else:
            score_modifier = 0
        
        return FilterResult(
            passed=True,
            score_modifier=score_modifier,
            metadata={'word_count': word_count}
        )


class ViewsRequirementFilter(FastFilter):
    """
    Фильтр по минимальному количеству просмотров
    
    Из Postopus: Просмотры = engagement = интересность
    Без просмотров = никому не интересно
    """
    
    def __init__(self, min_views: int = 0):
        super().__init__(name="Minimum Views Check", priority=31)
        self.min_views = min_views
    
    async def apply(self, post: Any, context: dict) -> FilterResult:
        """Проверка минимальных просмотров"""
        if not hasattr(post, 'views'):
            # Нет информации о просмотрах - пропускаем
            return FilterResult(passed=True)
        
        views = post.views or 0
        
        if views < self.min_views:
            return FilterResult(
                passed=False,
                reason=f"Too few views: {views} (min {self.min_views})"
            )
        
        # Бонус за популярный контент
        if views > 100:
            score_modifier = min(views // 50, 15)  # До 15 бонусных баллов
        else:
            score_modifier = 0
        
        return FilterResult(
            passed=True,
            score_modifier=score_modifier,
            metadata={'views': views}
        )


class CategoryFilter(FastFilter):
    """
    Фильтр по категории контента
    
    Можно настроить какие категории разрешены/запрещены
    """
    
    def __init__(self, allowed_categories: List[str] = None, blocked_categories: List[str] = None):
        super().__init__(name="Category Filter", priority=71)
        self.allowed_categories = set(allowed_categories) if allowed_categories else None
        self.blocked_categories = set(blocked_categories) if blocked_categories else set()
    
    async def apply(self, post: Any, context: dict) -> FilterResult:
        """Фильтр по категории"""
        category = None
        
        if hasattr(post, 'ai_category') and post.ai_category:
            category = post.ai_category
        
        if not category:
            # Нет категории - пропускаем
            return FilterResult(passed=True)
        
        # Проверка заблокированных категорий
        if category in self.blocked_categories:
            return FilterResult(
                passed=False,
                reason=f"Blocked category: {category}"
            )
        
        # Проверка разрешенных категорий
        if self.allowed_categories and category not in self.allowed_categories:
            return FilterResult(
                passed=False,
                reason=f"Category not allowed: {category}"
            )
        
        return FilterResult(passed=True)

