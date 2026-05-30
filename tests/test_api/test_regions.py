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


def test_region_update_can_clear_parent_explicitly():
    """UI: saveRegion передаёт `parent_region_id: null` для «открепить регион
    от родителя». RegionUpdate должен это принять и pass-through на ORM."""
    payload = regions_api.RegionUpdate(
        name="МАЛМЫЖ - ИНФО",
        parent_region_id=None,
    )
    assert payload.name == "МАЛМЫЖ - ИНФО"
    assert payload.parent_region_id is None
    # `kind` остаётся None — не перетираем при partial update.
    assert payload.kind is None


def test_region_create_strana_no_parent():
    """strana — верх иерархии, не должна иметь parent (но pydantic это не
    enforce, валидация на уровне БД/UI). Проверяем что None принимается."""
    payload = regions_api.RegionCreate(
        code="rf",
        name="РОССИЯ - ИНФО",
        kind="strana",
        parent_region_id=None,
    )
    assert payload.kind == "strana"
    assert payload.parent_region_id is None


def test_region_response_with_full_hierarchy_payload():
    """Smoke: RegionResponse корректно десериализуется из словаря с kind +
    parent_region_id (миграция 015)."""
    resp = regions_api.RegionResponse(
        id=21,
        code="kirov_obl",
        name="КИРОВСКАЯ ОБЛАСТЬ - ИНФО",
        vk_group_id=-168170001,
        telegram_channel=None,
        neighbors=None,
        is_active=True,
        created_at="2026-05-27T07:00:00",
        kind="oblast",
        parent_region_id=None,
    )
    assert resp.kind == "oblast"
    assert resp.parent_region_id is None
    assert resp.id == 21


# --- vk_group_id sign normalization (миграция 017 + валидатор) -------------
# Инвариант: regions.vk_group_id хранится в owner-форме (отрицательный id).
# `tuzha` исторически попал положительным (239050321) — единственный из 17.
# Валидатор _to_negative_owner_id не даёт положительному id попасть в БД снова.


def test_region_create_normalizes_positive_vk_group_id():
    """Положительный «голый» id (как ввёл модератор для tuzha) → -abs."""
    payload = regions_api.RegionCreate(code="tuzha", name="ТУЖА - ИНФО", vk_group_id=239050321)
    assert payload.vk_group_id == -239050321


def test_region_create_keeps_negative_vk_group_id():
    """Уже корректный отрицательный id остаётся как есть (идемпотентно)."""
    payload = regions_api.RegionCreate(code="mi", name="МАЛМЫЖ - ИНФО", vk_group_id=-158787639)
    assert payload.vk_group_id == -158787639


def test_region_create_allows_none_vk_group_id():
    """vk_group_id опционален (группа может появиться позже) — None проходит."""
    payload = regions_api.RegionCreate(code="newraion", name="Новый район - ИНФО")
    assert payload.vk_group_id is None


def test_region_update_normalizes_positive_vk_group_id():
    """Тот же инвариант на update — фикс tuzha через /regions edit."""
    payload = regions_api.RegionUpdate(vk_group_id=239050321)
    assert payload.vk_group_id == -239050321


def test_region_update_none_vk_group_id_is_passthrough():
    """Partial update без vk_group_id не трогает поле (None pass-through,
    не превращается в 0/-0)."""
    payload = regions_api.RegionUpdate(name="ТУЖА - ИНФО")
    assert payload.vk_group_id is None
