"""
Core модули системы
"""

from .config import ContentTypeConfig, RegionConfig
from .context import ProcessingContext, RegionContext

__all__ = [
    "ProcessingContext",
    "RegionContext",
    "RegionConfig",
    "ContentTypeConfig",
]
