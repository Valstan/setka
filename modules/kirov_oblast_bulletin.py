"""Backward-compat wrapper для каскадной сводки Кировской области.

С 2026-05-27 областная сводка собирается универсальным модулем
``modules.cascaded_bulletin`` (на базе иерархии регионов
``strana → oblast → raion``, миграция 015). Старая логика «extract wall.refs
из текста сводок районов + wall.getById для исходных постов» удалена
полностью — она была хрупкой и на проде давала ``total_groups_checked=0``.

Новая логика проще: берём по 5 свежих постов со стены главного сообщества
каждого подчинённого района, фильтруем рекламу/религию/дубли через общий
pipeline, публикуем сводку в ``kirov_obl.vk_group_id``.

Этот модуль оставлен для двух call-site:
  * ``tasks.parsing_scheduler_tasks.parse_and_publish_theme`` —
    special-case ``region_code='kirov_obl' AND theme='oblast'`` (см. ниже).
  * Внешние тесты / скрипты, если такие были.

Параметры (``test_mode``, ``region_code``, ``theme``) пробрасываются как есть,
чтобы PR был минимально-инвазивным.
"""

from __future__ import annotations

from typing import Any, Dict

from sqlalchemy.ext.asyncio import AsyncSession

DEFAULT_REGION_CODE = "kirov_obl"
THEME_OBLAST = "oblast"


async def run_kirov_oblast_bulletin(
    session: AsyncSession,
    *,
    region_code: str = DEFAULT_REGION_CODE,
    theme: str = THEME_OBLAST,
    test_mode: bool = False,
) -> Dict[str, Any]:
    """Thin wrapper над :func:`modules.cascaded_bulletin.run_cascaded_bulletin`."""
    from modules.cascaded_bulletin import run_cascaded_bulletin

    return await run_cascaded_bulletin(
        session,
        region_code=region_code,
        theme=theme,
        test_mode=test_mode,
    )
