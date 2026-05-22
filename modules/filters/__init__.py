"""
Модульная система фильтрации постов
Вдохновлена опытом проекта Postopus
"""

from .base import BaseFilter, FilterResult
from .content import (
    BlacklistWordFilter,
    MediaDuplicateFilter,
    SpamPatternFilter,
    TextDuplicateFilter,
    TextLengthFilter,
)
from .pipeline import FilterPipeline
from .quality import CategoryFilter, TextQualityFilter, ViewsRequirementFilter
from .regional import NeighborRegionFilter, RegionalRelevanceFilter
from .structural import BlacklistIDFilter, DateFilter, OnlyMainNewsFilter, StructuralDuplicateFilter

__all__ = [
    "BaseFilter",
    "FilterResult",
    "FilterPipeline",
    "StructuralDuplicateFilter",
    "DateFilter",
    "BlacklistIDFilter",
    "OnlyMainNewsFilter",
    "TextDuplicateFilter",
    "MediaDuplicateFilter",
    "BlacklistWordFilter",
    "TextLengthFilter",
    "SpamPatternFilter",
    "RegionalRelevanceFilter",
    "NeighborRegionFilter",
    "TextQualityFilter",
    "ViewsRequirementFilter",
    "CategoryFilter",
]
