"""
Модульная система фильтрации постов
Вдохновлена опытом проекта Postopus
"""
from .base import BaseFilter, FilterResult
from .pipeline import FilterPipeline
from .structural import StructuralDuplicateFilter, DateFilter, BlacklistIDFilter, OnlyMainNewsFilter
from .content import TextDuplicateFilter, MediaDuplicateFilter, BlacklistWordFilter, TextLengthFilter, SpamPatternFilter
from .regional import RegionalRelevanceFilter, NeighborRegionFilter
from .quality import TextQualityFilter, ViewsRequirementFilter, CategoryFilter

__all__ = [
    'BaseFilter',
    'FilterResult',
    'FilterPipeline',
    'StructuralDuplicateFilter',
    'DateFilter',
    'BlacklistIDFilter',
    'OnlyMainNewsFilter',
    'TextDuplicateFilter',
    'MediaDuplicateFilter',
    'BlacklistWordFilter',
    'TextLengthFilter',
    'SpamPatternFilter',
    'RegionalRelevanceFilter',
    'NeighborRegionFilter',
    'TextQualityFilter',
    'ViewsRequirementFilter',
    'CategoryFilter',
]

