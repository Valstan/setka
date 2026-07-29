"""Tests for GET /api/regions/vk-links — список сообществ сети для копирования."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from web.api import regions as regions_api


def _region(code, name, vk_group_id, kind="raion", parent_region_id=None, rid=0):
    return SimpleNamespace(
        id=rid,
        code=code,
        name=name,
        kind=kind,
        vk_group_id=vk_group_id,
        parent_region_id=parent_region_id,
        is_active=True,
        center_city=None,
    )


def _db_returning(regions, members_rows=()):
    """Мок: первый execute — регионы, второй — строки (region_id, members)."""
    regions_result = MagicMock()
    regions_result.scalars.return_value.all.return_value = regions
    members_result = MagicMock()
    members_result.all.return_value = list(members_rows)
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[regions_result, members_result])
    return db


@pytest.mark.asyncio
async def test_vk_links_returns_blocks_and_flat_text():
    db = _db_returning(
        [
            _region("kirov_obl", "КИРОВСКАЯ ОБЛАСТЬ - ИНФО", -168170001, kind="oblast", rid=21),
            _region("mi", "МАЛМЫЖ - ИНФО", -158787639, parent_region_id=21, rid=1),
        ],
        members_rows=[(21, 677), (1, 3657)],
    )

    resp = await regions_api.get_vk_links(db=db)

    assert resp["total"] == 2
    assert resp["total_members"] == 677 + 3657
    assert [b["title"] for b in resp["blocks"]] == ["Кировская область"]
    assert resp["text"].splitlines() == [
        "Кировская область:",
        "Кировская область ИНФО — 677 — https://vk.com/club168170001",
        "Малмыж ИНФО — 3657 — https://vk.com/club158787639",
    ]


@pytest.mark.asyncio
async def test_vk_links_block_carries_its_own_text():
    """У блока свой text — кнопка «копировать блок» не пересобирает его в JS."""
    db = _db_returning(
        [_region("tatarstan_obl", "ТАТАРСТАН - ИНФО", -239149826, kind="oblast", rid=22)]
    )

    resp = await regions_api.get_vk_links(db=db)

    assert resp["blocks"][0]["text"] == (
        "Татарстан:\nТатарстан ИНФО — https://vk.com/club239149826"
    )


@pytest.mark.asyncio
async def test_vk_links_empty_when_nothing_published():
    resp = await regions_api.get_vk_links(db=_db_returning([]))
    assert resp == {"blocks": [], "text": "", "total": 0, "total_members": 0}


def test_vk_links_route_declared_before_region_code_catch_all():
    """Порядок роутов: «/vk-links» обязан идти ДО «/{region_code}».

    Иначе FastAPI примет «vk-links» за код региона и эндпоинт станет
    недостижимым (та же грабля, что у «/suggest-neighbors»).
    """
    paths = [r.path for r in regions_api.router.routes]
    assert paths.index("/vk-links") < paths.index("/{region_code}")
