"""Tests for /api/regions — иерархия regions (kind, parent_region_id, миграция 015)."""

from __future__ import annotations

import pytest

from web.api import regions as regions_api


def test_region_kinds_constant():
    assert regions_api.REGION_KINDS == ("raion", "oblast", "strana")


def test_region_create_default_kind_is_raion():
    """Backward-compat: existing wizards и API-calls без поля `kind`
    создают raion (как было до миграции 015)."""
    payload = regions_api.RegionCreate(code="newraion", name="Новый район - ИНФО")
    assert payload.kind == "raion"
    assert payload.parent_region_id is None


def test_region_create_accepts_oblast_with_parent():
    payload = regions_api.RegionCreate(
        code="kirov_obl",
        name="КИРОВСКАЯ ОБЛАСТЬ - ИНФО",
        vk_group_id=-168170001,
        kind="oblast",
        parent_region_id=None,
    )
    assert payload.kind == "oblast"


def test_region_create_accepts_strana():
    payload = regions_api.RegionCreate(
        code="rf",
        name="РОССИЯ - ИНФО",
        vk_group_id=-1,
        kind="strana",
    )
    assert payload.kind == "strana"


def test_region_create_accepts_raion_with_parent():
    payload = regions_api.RegionCreate(
        code="mi",
        name="МАЛМЫЖ - ИНФО",
        vk_group_id=-158787639,
        kind="raion",
        parent_region_id=99,
    )
    assert payload.parent_region_id == 99


def test_region_create_rejects_unknown_kind():
    with pytest.raises(Exception):
        regions_api.RegionCreate(code="x", name="X", kind="federation")


def test_region_update_partial_kind_only():
    payload = regions_api.RegionUpdate(kind="oblast")
    assert payload.kind == "oblast"
    assert payload.parent_region_id is None
    # `name` остаётся None — partial update, не перетираем.
    assert payload.name is None


def test_region_update_rejects_unknown_kind():
    with pytest.raises(Exception):
        regions_api.RegionUpdate(kind="planeta")


def test_region_response_defaults():
    """RegionResponse имеет default kind=raion и parent_region_id=None
    для backward-compat при чтении старых записей до миграции 015."""
    resp = regions_api.RegionResponse(
        id=1,
        code="mi",
        name="МАЛМЫЖ - ИНФО",
        vk_group_id=-158787639,
        telegram_channel=None,
        neighbors=None,
        is_active=True,
        created_at="2026-05-27T00:00:00",
    )
    assert resp.kind == "raion"
    assert resp.parent_region_id is None
