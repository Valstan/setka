"""Unit tests for ``scripts/discover_scan.py`` — read-only VK community scanner.

Покрываем (а) чистые функции парсинга/стемминга/мерджа без сети и (б) сетевые
источники для районов (репосты главной, newsfeed.search, краулинг подписок) с
подменённым ``_vk_call`` — реальные VK-вызовы не делаются.

Скрипт — CLI-утилита вне устанавливаемого пакета (см. pyproject
``[tool.setuptools.packages.find].exclude``), поэтому грузим его напрямую через
importlib, как в ``tests/test_migrate.py``.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

_spec = importlib.util.spec_from_file_location(
    "discover_scan", REPO_ROOT / "scripts" / "discover_scan.py"
)
ds = importlib.util.module_from_spec(_spec)
sys.modules["discover_scan"] = ds
_spec.loader.exec_module(ds)


# --------------------------------------------------------------------------- #
# Чистые функции
# --------------------------------------------------------------------------- #


def test_parse_post_refs_extracts_groups_and_hashtags():
    text = (
        "Репост от [club123|ДК Малмыжа] и [public456|Библиотека]. "
        "Подписывайтесь @malmig_info и на vk.com/novosti_malmyzh. "
        "Ссылка на пост vk.com/wall-1_2 и профиль vk.com/id777. #малмыж #культура"
    )
    refs = ds.parse_post_refs(text)
    assert "club123" in refs["group_refs"]
    assert "club456" in refs["group_refs"]
    assert "malmig_info" in refs["group_refs"]
    assert "novosti_malmyzh" in refs["group_refs"]
    # wall-ссылки и id-профили — не сообщества
    assert not any(r.startswith("wall") for r in refs["group_refs"])
    assert "id777" not in refs["group_refs"]
    assert refs["hashtags"] == {"малмыж", "культура"}


def test_parse_post_refs_empty():
    refs = ds.parse_post_refs("")
    assert refs["group_refs"] == set()
    assert refs["hashtags"] == set()


def test_extract_repost_owner_ids_only_groups():
    items = [
        {"copy_history": [{"owner_id": -111}]},  # репост сообщества
        {"copy_history": [{"owner_id": 222}]},  # репост пользователя — игнор
        {"text": "без репоста"},
        {"copy_history": [{"owner_id": -111}, {"owner_id": -333}]},
    ]
    # хелпер возвращает все вхождения (частота репоста = сила партнёрства);
    # дедуп делает вызывающий harvest_main_group через sorted(set(...))
    assert ds.extract_repost_owner_ids(items) == [111, 111, 333]


@pytest.mark.parametrize(
    "word,expected",
    [
        ("Малмыж", "малмыж"),  # хвост-согласная — без изменений
        ("Савали", "савал"),  # срезали хвостовую 'и'
        ("Рожки", "рожк"),
        ("ДК", ""),  # короче 3 — отбрасываем
    ],
)
def test_make_stem(word, expected):
    assert ds.make_stem(word) == expected


def test_locality_stems_splits_multiword():
    stems = ds.locality_stems(["Мари-Малмыж", "Новая Смаиль"])
    assert "малмыж" in stems
    assert "смаиль" in stems
    assert "нов" in stems  # «Новая» → «нов»


def test_count_matched_localities():
    stems = ["рожк", "малмыж", "савал"]
    n = ds.count_matched_localities("СДК села Рожки Малмыжского района", stems)
    assert n == 2  # рожк + малмыж, савал не встретился


def test_parse_localities_dedup_and_split():
    out = ds.parse_localities("Малмыж, Савали\nКалинино;Арык, Малмыж")
    assert out == ["Малмыж", "Савали", "Калинино", "Арык"]


def test_parse_localities_empty():
    assert ds.parse_localities(None) == []
    assert ds.parse_localities("") == []


# --------------------------------------------------------------------------- #
# add_candidate — дедуп + мердж via
# --------------------------------------------------------------------------- #


def test_add_candidate_merges_via_and_dedups():
    seen: dict = {}
    ds.add_candidate(seen, 100, "info_repost", name="Группа")
    ds.add_candidate(seen, 100, "mention")  # тот же id, другой источник
    ds.add_candidate(seen, 200, "newsfeed")
    assert set(seen.keys()) == {100, 200}
    assert seen[100]["via"] == ["info_repost", "mention"]
    assert seen[100]["name"] == "Группа"


def test_add_candidate_fills_missing_fields_only():
    seen: dict = {}
    ds.add_candidate(seen, 100, "info_repost")  # без name
    ds.add_candidate(seen, 100, "mention", name="Заполнили потом", members_count=5)
    assert seen[100]["name"] == "Заполнили потом"
    assert seen[100]["members_count"] == 5


def test_add_candidate_ignores_users_and_garbage():
    seen: dict = {}
    ds.add_candidate(seen, 0, "x")
    ds.add_candidate(seen, -5, "x")  # отрицательный → не сообщество в groups.search-форме
    ds.add_candidate(seen, None, "x")
    ds.add_candidate(seen, "не-число", "x")
    assert seen == {}


# --------------------------------------------------------------------------- #
# Сетевые источники — с подменённым _vk_call
# --------------------------------------------------------------------------- #


def test_harvest_main_group(monkeypatch):
    captured = {}

    def fake_vk_call(method, token, params, retries=2):
        captured["method"] = method
        captured["owner_id"] = params.get("owner_id")
        return {
            "items": [
                {
                    "text": "Новость дня #малмыж см. [club555|Партнёр]",
                    "copy_history": [{"owner_id": -700, "text": "оригинал @sport_malmyzh"}],
                },
                {"text": "Без репоста, но vk.com/dk_malmyzh"},
            ]
        }

    monkeypatch.setattr(ds, "_vk_call", fake_vk_call)
    repost_ids, mention_refs, hashtags = ds.harvest_main_group("tok", -158787639, 80)

    assert captured["method"] == "wall.get"
    assert captured["owner_id"] == -158787639  # знак нормализован к owner-форме
    assert repost_ids == [700]
    assert "club555" in mention_refs
    assert "sport_malmyzh" in mention_refs  # из текста оригинала репоста
    assert "dk_malmyzh" in mention_refs
    assert "малмыж" in hashtags


def test_newsfeed_search_collects_group_owners(monkeypatch):
    def fake_vk_call(method, token, params, retries=2):
        assert method == "newsfeed.search"
        return {
            "items": [
                {"from_id": -10},  # сообщество
                {"from_id": 20},  # пользователь — игнор
                {"source_id": -30},  # запасное поле
                {"owner_id": -10},  # дубль
            ]
        }

    monkeypatch.setattr(ds, "_vk_call", fake_vk_call)
    found = ds.newsfeed_search("tok", "Малмыж", 80, start_time=0)
    assert found == {10, 30}


def test_crawl_subscriptions_respects_caps(monkeypatch):
    calls = {"getMembers": 0, "get": 0}

    def fake_vk_call(method, token, params, retries=2):
        if method == "groups.getMembers":
            calls["getMembers"] += 1
            assert params["filter"] == "managers"
            return {"items": [{"id": 5, "role": "administrator"}, {"id": 6}, {"id": -1}]}
        if method == "groups.get":
            calls["get"] += 1
            return {"items": [101, 102, calls["get"]]}
        raise AssertionError(f"unexpected method {method}")

    monkeypatch.setattr(ds, "_vk_call", fake_vk_call)
    found = ds.crawl_subscriptions(
        "tok", seed_group_ids=[-158787639, 700], max_seeds=1, max_managers=1
    )
    assert calls["getMembers"] == 1  # max_seeds=1
    assert calls["get"] == 1  # max_managers=1
    assert {101, 102}.issubset(found)


def test_harvest_main_group_links(monkeypatch):
    def fake_vk_call(method, token, params, retries=2):
        assert method == "groups.getById"
        assert params.get("fields") == "links"
        return {
            "groups": [
                {
                    "id": 158787639,
                    "links": [
                        {"url": "https://vk.com/inter_malmiz", "name": "Академия футбола"},
                        {"url": "https://vk.com/public207560759", "name": "Админ. поселения"},
                        {"url": "https://malmyzh-r43.gosweb.gosuslugi.ru/", "name": "госуслуги"},
                        {"url": "https://vk.com/id777", "name": "профиль — не сообщество"},
                    ],
                }
            ]
        }

    monkeypatch.setattr(ds, "_vk_call", fake_vk_call)
    refs = ds.harvest_main_group_links("tok", -158787639)
    assert "inter_malmiz" in refs
    assert "public207560759" in refs
    # внешние URL и id-профили — не сообщества
    assert "id777" not in refs
    assert all("gosuslugi" not in r for r in refs)


def test_resolve_group_refs_filters_to_groups(monkeypatch):
    def fake_vk_call(method, token, params, retries=2):
        assert method == "groups.getById"
        # VK вернёт только сообщества; пользовательские домены молча выпадают
        return {"groups": [{"id": 555, "name": "Партнёр", "screen_name": "partner"}]}

    monkeypatch.setattr(ds, "_vk_call", fake_vk_call)
    groups = ds.resolve_group_refs("tok", ["club555", "some_user"], "members_count")
    assert len(groups) == 1
    assert groups[0]["id"] == 555
