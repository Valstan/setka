"""Tests for ContextFactory.create_from_region.

Эта ветка относится к восстановленным F821-импортам (2026-05-22):
`from modules.core.context import RegionContext` / `ProcessingContext`
пропали при автоматической legacy-зачистке, потом восстановлены.
Runtime-вызовов нет в проекте; тест нужен чтобы зафиксировать корректность
и поймать NameError, если импорты снова пропадут.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from database.models import Region
from modules.core.config import ContextFactory
from modules.core.context import ProcessingContext


@pytest.mark.asyncio
async def test_create_from_region_success(mock_db_session):
    """Region найден → возвращает ProcessingContext с заполненными полями."""
    region = Region(
        id=42,
        code="mi",
        name="Тестовый регион",
        vk_group_id=-12345,
        telegram_channel="@test_ch",
        neighbors="советск, лебяж , уржум",
        is_active=True,
        config={},
    )

    # mock_db_session.execute → result со scalar_one_or_none(region)
    result_mock = MagicMock()
    result_mock.scalar_one_or_none = MagicMock(return_value=region)
    mock_db_session.execute = AsyncMock(return_value=result_mock)

    ctx = await ContextFactory.create_from_region(
        region_id=42, content_type="novost", db_session=mock_db_session
    )

    assert isinstance(ctx, ProcessingContext)
    assert ctx.content_type == "novost"
    assert ctx.db_session is mock_db_session
    assert ctx.region.region_id == 42
    assert ctx.region.region_code == "mi"
    assert ctx.region.region_name == "Тестовый регион"
    assert ctx.region.vk_target_group == -12345
    assert ctx.region.telegram_channel == "@test_ch"
    # neighbors должны быть split по запятой + strip-нуты
    assert ctx.region.neighbors == ["советск", "лебяж", "уржум"]


@pytest.mark.asyncio
async def test_create_from_region_not_found_raises_value_error(mock_db_session):
    """Region не найден в БД → ValueError с понятным сообщением."""
    result_mock = MagicMock()
    result_mock.scalar_one_or_none = MagicMock(return_value=None)
    mock_db_session.execute = AsyncMock(return_value=result_mock)

    with pytest.raises(ValueError, match="Region 999 not found"):
        await ContextFactory.create_from_region(
            region_id=999, content_type="novost", db_session=mock_db_session
        )


@pytest.mark.asyncio
async def test_create_from_region_empty_neighbors(mock_db_session):
    """neighbors=None или пустой → пустой список (не падает)."""
    region = Region(
        id=1,
        code="solo",
        name="Без соседей",
        vk_group_id=-100,
        telegram_channel=None,
        neighbors=None,
        is_active=True,
        config={},
    )
    result_mock = MagicMock()
    result_mock.scalar_one_or_none = MagicMock(return_value=region)
    mock_db_session.execute = AsyncMock(return_value=result_mock)

    ctx = await ContextFactory.create_from_region(
        region_id=1, content_type="novost", db_session=mock_db_session
    )
    assert ctx.region.neighbors == []
