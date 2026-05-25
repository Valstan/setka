"""Unit tests for modules/discovery/vk_search.py — composite discovery."""

from __future__ import annotations

from unittest.mock import MagicMock

from modules.discovery.vk_search import (
    CATEGORY_KEYWORDS,
    _build_search_plan,
    _count_localities_in_text,
    _make_stem,
    discover_for_region,
)


def _make_client(search_results=None, enrich_results=None):
    client = MagicMock()
    client.search_groups.side_effect = (
        search_results if isinstance(search_results, list) else (lambda **_: search_results or [])
    )
    client.get_groups_by_ids.return_value = enrich_results or []
    return client


# ───────── _make_stem / _count_localities_in_text ─────────


def test_stem_short_names_kept_as_is():
    assert _make_stem("Уя") == "уя"
    assert _make_stem("Юм") == "юм"


def test_stem_strips_trailing_vowels():
    """Стем отбрасывает конечные гласные до согласной."""
    assert _make_stem("Тужа") == "туж"  # "а" → стоп на "ж"
    assert _make_stem("Шешурга") == "шешург"  # "а" → стоп на "г"
    assert _make_stem("Михайловское") == "михайловск"  # "е", "о" → стоп на "к"
    assert _make_stem("Малмыж") == "малмыж"  # уже на согласной — не трогаем


def test_count_localities_matches_declensions():
    """Стем "туж" должен матчить разные падежи "Тужа"."""
    stems = [_make_stem("Тужа")]
    assert _count_localities_in_text(stems, "Подслушано в Туже") == 1
    assert _count_localities_in_text(stems, "Тужинский район") == 1
    assert _count_localities_in_text(stems, "Тужанские новости") == 1
    assert _count_localities_in_text(stems, "Едем в Тужу за грибами") == 1


def test_count_localities_left_word_boundary_protects_from_false_positive():
    """Левый \\b защищает от "батужа" / "котужа" — не должно матчить "туж"."""
    stems = [_make_stem("Тужа")]
    assert _count_localities_in_text(stems, "Картужа Карловы Вары") == 0


def test_count_localities_multiple_localities_sum():
    stems = [_make_stem("Тужа"), _make_stem("Шешурга")]
    assert _count_localities_in_text(stems, "Новости Тужи и Шешурги") == 2
    assert _count_localities_in_text(stems, "Только Тужа") == 1
    assert _count_localities_in_text(stems, "Ничего по теме") == 0


def test_count_localities_empty_inputs():
    assert _count_localities_in_text([], "Тужа") == 0
    assert _count_localities_in_text(["туж"], "") == 0
    assert _count_localities_in_text(["туж"], None) == 0  # type: ignore[arg-type]


# ───────── _build_search_plan ─────────


def test_search_plan_starts_with_geo_query():
    plan = _build_search_plan(center_city="Малмыж", localities=[], keywords=[])
    assert plan[0] == ("Малмыж", "geo_search", True)


def test_search_plan_localities_before_keywords():
    plan = _build_search_plan(
        center_city="Малмыж",
        localities=["Шешурга", "Михайловское"],
        keywords=["новости"],
    )
    sources = [s for _, s, _ in plan]
    assert sources[0] == "geo_search"
    assert sources[1].startswith("loc:")
    assert sources[2].startswith("loc:")
    assert sources[3].startswith("kw:")


def test_search_plan_localities_have_no_city_id():
    """Локалитет-запросы должны идти БЕЗ city_id — мелкие сельские паблики
    часто помечены городом-центром района, не каждой деревней."""
    plan = _build_search_plan(
        center_city="Малмыж",
        localities=["Шешурга"],
        keywords=[],
    )
    geo = [t for t in plan if t[1] == "geo_search"][0]
    loc = [t for t in plan if t[1].startswith("loc:")][0]
    assert geo[2] is True  # use_city_id
    assert loc[2] is False


def test_search_plan_falls_back_to_category_keywords_when_keywords_empty():
    """Если кастомные keywords не заданы — берём flat-список CATEGORY_KEYWORDS."""
    plan = _build_search_plan(center_city="X", localities=[], keywords=[])
    sources = [s for _, s, _ in plan]
    # 1 geo + N keywords из всех категорий
    expected_kw = sum(len(v) for v in CATEGORY_KEYWORDS.values())
    # CATEGORY_KEYWORDS могут иметь дубликаты между категориями (сейчас нет),
    # но dedup по lower применяется — проверяем нижнюю границу.
    kw_count = sum(1 for s in sources if s.startswith("kw:"))
    assert kw_count <= expected_kw
    assert kw_count >= 1


def test_search_plan_dedup_localities_case_insensitive():
    plan = _build_search_plan(center_city="X", localities=["Тужа", "тужа", "ТУЖА"], keywords=[])
    loc_queries = [q for q, s, _ in plan if s.startswith("loc:")]
    assert len(loc_queries) == 1


def test_search_plan_no_center_city_still_works_with_localities():
    plan = _build_search_plan(center_city="", localities=["Тужа"], keywords=["новости"])
    sources = [s for _, s, _ in plan]
    assert "geo_search" not in sources
    assert any(s.startswith("loc:") for s in sources)


# ───────── discover_for_region ─────────


def test_discover_empty_inputs_returns_empty():
    client = MagicMock()
    assert discover_for_region(client=client, center_city="  ", localities=[]) == []
    client.search_groups.assert_not_called()


def test_discover_calls_search_for_geo_and_each_localitet():
    client = MagicMock()
    client.search_groups.return_value = []
    client.get_groups_by_ids.return_value = []

    discover_for_region(
        client=client,
        center_city="Тужа",
        localities=["Шешурга", "Михайловское"],
        keywords=[],
    )
    # 1 geo + 2 localities + N CATEGORY_KEYWORDS fallback
    kw_count = sum(len(v) for v in CATEGORY_KEYWORDS.values())
    assert client.search_groups.call_count >= 1 + 2 + kw_count - 5  # допуск на dedup


def test_discover_passes_city_id_only_for_geo_search():
    """Geo-search использует city_id; локалитеты и keyword — нет."""
    client = MagicMock()
    client.search_groups.return_value = []
    client.get_groups_by_ids.return_value = []

    discover_for_region(
        client=client,
        center_city="Тужа",
        vk_city_id=314,
        localities=["Шешурга"],
        keywords=["новости"],
    )
    calls = client.search_groups.call_args_list
    assert calls[0].kwargs["city_id"] == 314  # geo
    for c in calls[1:]:
        assert c.kwargs["city_id"] is None  # locality + keyword


def test_discover_hard_filter_drops_irrelevant_candidates():
    """Кандидат без localities в name+description отбрасывается, если
    localities были заданы."""
    client = MagicMock()
    client.search_groups.return_value = [
        {"id": 1, "name": "Подслушано Тужа"},
        {"id": 2, "name": "Море Парк Киров"},  # нет тужи — отбросить
        {"id": 3, "name": "ЧП Тужинский район"},  # склонение
    ]
    client.get_groups_by_ids.return_value = []

    groups = discover_for_region(
        client=client,
        center_city="Тужа",
        localities=["Тужа"],
        keywords=[],
    )
    assert sorted(g.vk_id for g in groups) == [1, 3]


def test_discover_without_localities_skips_relevance_filter():
    """Backwards-compat: если localities пуст — фильтр не применяется,
    возвращаются все найденные."""
    client = MagicMock()
    client.search_groups.return_value = [
        {"id": 1, "name": "Подслушано Тужа"},
        {"id": 2, "name": "Море Парк Киров"},
    ]
    client.get_groups_by_ids.return_value = []

    groups = discover_for_region(client=client, center_city="X", localities=[], keywords=[])
    assert sorted(g.vk_id for g in groups) == [1, 2]


def test_discover_sorts_by_matched_localities_first():
    """Паблик с тремя топонимами района обгонит крупный с одним."""
    client = MagicMock()
    client.search_groups.return_value = [
        {"id": 1, "name": "Тужа"},  # 1 match
        {"id": 2, "name": "Тужа и Шешурга и Михайловское"},  # 3 matches
        {"id": 3, "name": "Тужа Шешурга"},  # 2 matches
    ]
    client.get_groups_by_ids.return_value = [
        {"id": 1, "members_count": 10000},
        {"id": 2, "members_count": 100},
        {"id": 3, "members_count": 500},
    ]

    groups = discover_for_region(
        client=client,
        center_city="X",
        localities=["Тужа", "Шешурга", "Михайловское"],
        keywords=[],
    )
    assert [g.vk_id for g in groups] == [2, 3, 1]


def test_discover_sort_falls_back_to_members_count_at_equal_localities():
    client = MagicMock()
    client.search_groups.return_value = [
        {"id": 1, "name": "Тужа one"},
        {"id": 2, "name": "Тужа two"},
    ]
    client.get_groups_by_ids.return_value = [
        {"id": 1, "members_count": 100},
        {"id": 2, "members_count": 9000},
    ]
    groups = discover_for_region(
        client=client,
        center_city="X",
        localities=["Тужа"],
        keywords=[],
    )
    assert [g.vk_id for g in groups] == [2, 1]


def test_discover_dedup_by_vk_id_first_source_wins():
    """Same group via geo и locality — discovered_via = geo_search."""
    client = MagicMock()

    def search_side_effect(*, query, city_id=None, count=100, offset=0):
        if query == "Тужа":
            return [{"id": 1, "name": "Подслушано Тужа"}]
        return []

    client.search_groups.side_effect = search_side_effect
    client.get_groups_by_ids.return_value = []

    groups = discover_for_region(
        client=client,
        center_city="Тужа",
        localities=["Тужа"],
        keywords=[],
    )
    assert len(groups) == 1
    assert groups[0].vk_id == 1
    # Первый запрос — geo_search (с city_id), он выигрывает в дедупе.
    assert groups[0].discovered_via == "geo_search"


def test_discover_filters_out_excluded_vk_ids():
    client = MagicMock()
    client.search_groups.return_value = [
        {"id": 10, "name": "Already added Тужа"},
        {"id": 20, "name": "New one Тужа"},
    ]
    client.get_groups_by_ids.return_value = []

    groups = discover_for_region(
        client=client,
        center_city="X",
        localities=["Тужа"],
        keywords=[],
        exclude_vk_ids=[10],
    )
    assert [g.vk_id for g in groups] == [20]


def test_discover_handles_negative_vk_id_in_exclude_via_abs():
    client = MagicMock()
    client.search_groups.return_value = [{"id": 10, "name": "A Тужа"}]
    client.get_groups_by_ids.return_value = []

    groups = discover_for_region(
        client=client,
        center_city="X",
        localities=["Тужа"],
        keywords=[],
        exclude_vk_ids=[-10],
    )
    assert groups == []


def test_discover_enriches_from_groups_get_by_id_once():
    """get_groups_by_ids зовётся ровно один раз для всех уникальных id."""
    client = MagicMock()
    client.search_groups.side_effect = [
        [{"id": 1, "name": "A Тужа"}, {"id": 2, "name": "B Тужа"}],
        [{"id": 3, "name": "C Тужа"}],
    ]
    client.get_groups_by_ids.return_value = [
        {"id": 1, "name": "A Тужа", "description": "desc-1", "members_count": 5000},
        {"id": 2, "name": "B Тужа", "members_count": 100},
    ]

    groups = discover_for_region(
        client=client,
        center_city="X",
        localities=["Тужа"],
        keywords=["новости"],  # 1 keyword search
    )
    assert client.get_groups_by_ids.call_count == 1
    ids_arg = client.get_groups_by_ids.call_args[0][0]
    assert sorted(ids_arg) == [1, 2, 3]

    by_id = {g.vk_id: g for g in groups}
    assert by_id[1].description == "desc-1"
    assert by_id[1].members_count == 5000
    assert by_id[2].members_count == 100


def test_discover_skips_items_with_zero_id():
    client = MagicMock()
    client.search_groups.return_value = [
        {"id": 0, "name": "Bad Тужа"},
        {"id": 1, "name": "Good Тужа"},
    ]
    client.get_groups_by_ids.return_value = []
    groups = discover_for_region(client=client, center_city="X", localities=["Тужа"], keywords=[])
    assert [g.vk_id for g in groups] == [1]


def test_discover_truncates_to_top_n_after_sort():
    """При >max_candidates групп берём топ-N (после relevance + sort)."""
    client = MagicMock()
    items = [{"id": i, "name": f"G{i} Тужа"} for i in range(1, 11)]
    client.search_groups.return_value = items
    client.get_groups_by_ids.return_value = [
        {"id": i, "members_count": i * 100} for i in range(1, 11)
    ]
    groups = discover_for_region(
        client=client,
        center_city="X",
        localities=["Тужа"],
        keywords=[],
        max_candidates=3,
    )
    assert len(groups) == 3
    # Все имеют по 1 matched_localities, fallback на members_count desc.
    assert [g.vk_id for g in groups] == [10, 9, 8]


def test_discover_does_not_fetch_wall_posts_inline():
    """Sync `discover_for_region` НЕ тянет wall.get — это делает async
    `_ai_categorize_all`."""
    client = MagicMock()
    client.search_groups.return_value = [
        {"id": 1, "name": "A Тужа"},
        {"id": 2, "name": "B Тужа"},
    ]
    client.get_groups_by_ids.return_value = []

    discover_for_region(client=client, center_city="X", localities=["Тужа"], keywords=[])
    client.get_wall_posts.assert_not_called()
