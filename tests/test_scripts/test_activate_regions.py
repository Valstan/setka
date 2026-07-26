"""Unit tests для ``scripts/activate_regions.py``.

Покрываем два чистых решения активации района:

* ``parse_target`` — как читается аргумент CLI (код / код=адрес / код=id);
* ``evaluate_readiness`` — что блокирует включение, а что лишь предупреждает.

Граница «блокер vs предупреждение» здесь и есть содержательная часть: блокер
означает «включим и получим неработающий район» (нет группы, пустой пул),
предупреждение — «район заработает, но часть функций не сразу» (нет
community-токена → публикация пойдёт каскадом; пустые neighbors → не будет
соседского обмена). Перепутать их дорого в обе стороны: лишний блокер
останавливает раскатку, пропущенный — даёт тихо неработающий регион.

Скрипт — CLI-утилита вне устанавливаемого пакета, грузим напрямую через
importlib (как ``test_seed_region_communities.py``). Импорты БД/VK в нём
ленивые, поэтому модуль-левел безопасен.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

_spec = importlib.util.spec_from_file_location(
    "activate_regions", REPO_ROOT / "scripts" / "activate_regions.py"
)
activate_regions = importlib.util.module_from_spec(_spec)
sys.modules["activate_regions"] = activate_regions
_spec.loader.exec_module(activate_regions)


def _region(**overrides):
    """Скелетный район: неактивен, без группы, соседи заполнены."""
    defaults = dict(id=1, code="omutninsk", is_active=False, vk_group_id=None, neighbors="uni")
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _evaluate(**overrides):
    """Готовый к активации район; тест меняет ровно один факт."""
    params = dict(
        target=activate_regions.Target(code="omutninsk", screen_name="omutninsk_info43"),
        region=_region(),
        group_id=-240000001,
        group_title="ОМУТНИНСК - ИНФО",
        pool_size=43,
        has_community_token=True,
        neighbors="uni",
        token_is_admin=True,
        force=False,
    )
    params.update(overrides)
    return activate_regions.evaluate_readiness(**params)


# --- parse_target -----------------------------------------------------------


def test_bare_code_uses_default_screen_name():
    """Голый код → короткий адрес по канону батча №1."""
    target = activate_regions.parse_target("omutninsk")
    assert target.code == "omutninsk"
    assert target.screen_name == "omutninsk_info43"
    assert target.group_id is None


def test_explicit_screen_name_wins():
    """Адрес группы может не совпадать с кодом района (Кирс ≠ verhnekame)."""
    target = activate_regions.parse_target("verhnekame=kirs_info43")
    assert target.code == "verhnekame"
    assert target.screen_name == "kirs_info43"


@pytest.mark.parametrize("raw", ["verhnekame=-240386781", "verhnekame=240386781"])
def test_numeric_hint_is_group_id_and_always_negative(raw):
    """id принимаем в любом знаке, храним как в regions — отрицательным."""
    target = activate_regions.parse_target(raw)
    assert target.group_id == -240386781
    assert target.screen_name is None


def test_empty_code_rejected():
    activate_regions.parse_target("omutninsk")  # sanity: валидный не бросает
    with pytest.raises(ValueError):
        activate_regions.parse_target("=kirs_info43")


# --- evaluate_readiness: блокеры -------------------------------------------


def test_all_facts_good_is_ready():
    verdict = _evaluate()
    assert verdict.ready
    assert verdict.blockers == []
    assert verdict.warnings == []


def test_missing_group_blocks():
    """Группы нет в VK — активировать некуда, это главный блокер."""
    verdict = _evaluate(group_id=None, group_title=None)
    assert not verdict.ready
    assert any("группа не найдена" in b for b in verdict.blockers)


def test_unknown_region_blocks_and_stops_early():
    """Нет региона — дальше проверять нечего, остальные факты не смотрим."""
    verdict = _evaluate(region=None)
    assert not verdict.ready
    assert len(verdict.blockers) == 1
    assert "нет в БД" in verdict.blockers[0]


def test_empty_pool_blocks():
    """Активный район без источников выпускал бы пустые сводки."""
    verdict = _evaluate(pool_size=0)
    assert not verdict.ready
    assert any("пул источников пуст" in b for b in verdict.blockers)


def test_active_region_with_other_group_blocks():
    """Уже активен с другой группой — молча перенаправить сводки нельзя."""
    verdict = _evaluate(region=_region(is_active=True, vk_group_id=-111222333))
    assert not verdict.ready
    assert any("уже активен с другой группой" in b for b in verdict.blockers)


def test_force_allows_regroup():
    """С --force перезапись разрешена — осознанный ход оператора."""
    verdict = _evaluate(region=_region(is_active=True, vk_group_id=-111222333), force=True)
    assert verdict.ready


def test_same_group_reactivation_is_not_a_blocker():
    """Повторный прогон по уже активному району идемпотентен."""
    verdict = _evaluate(region=_region(is_active=True, vk_group_id=-240000001))
    assert verdict.ready


# --- evaluate_readiness: предупреждения ------------------------------------


def test_missing_community_token_warns_but_activates():
    """Токена сообщества нет → публикация каскадом, но район включаем."""
    verdict = _evaluate(has_community_token=False)
    assert verdict.ready
    assert any("community-токен" in w for w in verdict.warnings)


def test_empty_neighbors_warns_but_activates():
    """Без neighbors район живёт, но выпадает из соседского обмена."""
    verdict = _evaluate(region=_region(neighbors=""), neighbors="")
    assert verdict.ready
    assert any("neighbors" in w for w in verdict.warnings)


def test_non_admin_token_warns():
    verdict = _evaluate(token_is_admin=False)
    assert verdict.ready
    assert any("администрирует" in w for w in verdict.warnings)


def test_unknown_admin_status_is_silent():
    """Проверка админства недоступна (VK не ответил) → не выдумываем warning."""
    verdict = _evaluate(token_is_admin=None)
    assert verdict.ready
    assert verdict.warnings == []


def test_missing_group_does_not_add_group_warnings():
    """Нет группы → не сыпать «нет ключа» и «не админ»: это следствия, не факты.

    Иначе единственный настоящий блокер («создайте группу») тонет в трёх
    строках шума на каждый район — ровно то, что делает отчёт нечитаемым при
    раскатке пачками.
    """
    verdict = _evaluate(
        group_id=None, group_title=None, has_community_token=False, token_is_admin=False
    )
    assert not verdict.ready
    assert not any("community-токен" in w for w in verdict.warnings)
    assert not any("администрирует" in w for w in verdict.warnings)
