"""
Модуль агрегации контента
Умное объединение похожих постов в дайджесты
"""
from .aggregator import NewsAggregator, AggregatedPost
from .clustering import PostClusterer

__all__ = [
    'NewsAggregator',
    'AggregatedPost',
    'PostClusterer',
]

