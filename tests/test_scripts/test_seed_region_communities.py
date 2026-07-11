"""Unit tests for ``scripts/seed_region_communities.py``.

Покрываем чистую функцию ``_split_by_community_confirmation`` — решение, кого
отсеять как не-сообщество (личный профиль / удалённый id) по подтверждённому
VK-множеству. Это seed-ветка защиты от протечки личных профилей в пул (парная
к фиксу ``_harvest_repost_owner_ids`` в discovery, brain 2026-06-30).

Скрипт — CLI-утилита вне устанавливаемого пакета, грузим напрямую через
importlib (как в ``tests/test_scripts/test_discover_scan.py``). Тяжёлые импорты
(БД / VK) в сидере ленивые — модуль-левел безопасен.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

_spec = importlib.util.spec_from_file_location(
    "seed_region_communities", REPO_ROOT / "scripts" / "seed_region_communities.py"
)
seed = importlib.util.module_from_spec(_spec)
sys.modules["seed_region_communities"] = seed
_spec.loader.exec_module(seed)


def _item(vk_abs: int, name: str = "grp"):
    return {"vk_abs": vk_abs, "category": "novost", "name": name, "screen_name": None}


def test_split_none_confirmed_keeps_all():
    """Валидация недоступна (None) → отсева нет, обратная совместимость."""
    parsed = [_item(111), _item(222)]
    keep, dropped = seed._split_by_community_confirmation(parsed, None)
    assert [p["vk_abs"] for p in keep] == [111, 222]
    assert dropped == []


def test_split_drops_ids_not_confirmed_as_community():
    """Личный профиль (333 не подтверждён VK как сообщество) отсеивается."""
    parsed = [_item(111), _item(222), _item(333, "личный профиль")]
    keep, dropped = seed._split_by_community_confirmation(parsed, {111, 222})
    assert [p["vk_abs"] for p in keep] == [111, 222]
    assert [p["vk_abs"] for p in dropped] == [333]


def test_split_empty_confirmed_drops_everything():
    """Пустое подтверждённое множество (VK ничего не признал сообществом) →
    ничего не сеем."""
    parsed = [_item(111), _item(222)]
    keep, dropped = seed._split_by_community_confirmation(parsed, set())
    assert keep == []
    assert [p["vk_abs"] for p in dropped] == [111, 222]


def test_split_empty_input():
    assert seed._split_by_community_confirmation([], {111}) == ([], [])
    assert seed._split_by_community_confirmation([], None) == ([], [])


def test_confirmed_ids_empty_input_returns_empty_set_without_vk():
    """Пустой вход id → пустое множество, VK не трогаем (возврат set, не None)."""
    assert seed._confirmed_community_ids([]) == set()
    assert seed._confirmed_community_ids([0, 0]) == set()
