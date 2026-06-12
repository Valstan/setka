"""Postopus-core: из старого слоя жив только scoring.calculate_post_score.

config.py/context.py (RegionContext/ProcessingContext/ContextFactory)
удалены деадкод-пакетом #036: их не использовал никто, кроме собственных
тестов (см. PR деадкод-пакета 3).
"""

from .scoring import calculate_post_score

__all__ = ["calculate_post_score"]
