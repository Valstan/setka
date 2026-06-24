"""
Модуль агрегации контента
Умное объединение похожих постов в сводки
"""

from .aggregator import AggregatedPost, NewsAggregator
from .clustering import PostClusterer

__all__ = [
    "NewsAggregator",
    "AggregatedPost",
    "PostClusterer",
]
