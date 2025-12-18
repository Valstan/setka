"""
Базовые классы для системы фильтрации
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional, Any
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


@dataclass
class FilterResult:
    """Результат применения фильтра"""
    passed: bool  # Прошел ли пост фильтр
    reason: Optional[str] = None  # Причина отсева (если не прошел)
    score_modifier: int = 0  # Изменение оценки поста
    metadata: dict = None  # Дополнительная информация
    
    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


class BaseFilter(ABC):
    """
    Базовый класс для всех фильтров
    
    Вдохновлен концепцией Pipeline фильтрации из Postopus:
    - Каждый фильтр - отдельная функция
    - Композиция фильтров
    - Легко добавлять новые
    """
    
    def __init__(self, name: str, priority: int = 50):
        """
        Args:
            name: Название фильтра
            priority: Приоритет (меньше = раньше выполняется)
        """
        self.name = name
        self.priority = priority
        self.stats = {
            'total_checked': 0,
            'passed': 0,
            'filtered': 0
        }
    
    @abstractmethod
    async def apply(self, post: Any, context: dict) -> FilterResult:
        """
        Применить фильтр к посту
        
        Args:
            post: Пост для проверки (может быть Post модель или dict)
            context: Контекст обработки (region, session, filters, etc)
            
        Returns:
            FilterResult с результатом проверки
        """
        pass
    
    def update_stats(self, result: FilterResult):
        """Обновить статистику фильтра"""
        self.stats['total_checked'] += 1
        if result.passed:
            self.stats['passed'] += 1
        else:
            self.stats['filtered'] += 1
    
    def get_stats(self) -> dict:
        """Получить статистику работы фильтра"""
        if self.stats['total_checked'] > 0:
            filter_rate = (self.stats['filtered'] / self.stats['total_checked']) * 100
        else:
            filter_rate = 0
        
        return {
            'name': self.name,
            'priority': self.priority,
            'total_checked': self.stats['total_checked'],
            'passed': self.stats['passed'],
            'filtered': self.stats['filtered'],
            'filter_rate': f"{filter_rate:.1f}%"
        }
    
    def reset_stats(self):
        """Сбросить статистику"""
        self.stats = {
            'total_checked': 0,
            'passed': 0,
            'filtered': 0
        }
    
    def __repr__(self):
        return f"<{self.__class__.__name__} '{self.name}' priority={self.priority}>"


class FastFilter(BaseFilter):
    """
    Быстрый фильтр (без обращения к БД)
    Используется для первичной отсечки
    """
    pass


class DBFilter(BaseFilter):
    """
    Фильтр с обращением к БД
    Используется для проверок требующих данные из БД
    """
    pass


class ExpensiveFilter(BaseFilter):
    """
    Дорогой фильтр (AI, внешние API)
    Используется только для прошедших базовые проверки
    """
    pass

