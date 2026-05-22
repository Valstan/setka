"""Unit tests for modules/discovery/vk_search.py — composite discovery."""

from __future__ import annotations

from unittest.mock import MagicMock

from modules.discovery.vk_search import CATEGORY_KEYWORDS, _build_search_plan, discover_for_region


def _make_client(search_results=None, enrich_results=None):
    client = MagicMock()
    client.search_groups.side_effect = (
        search_results if isinstance(search_results, list) else (lambda **_: search_results or [])
    )
    client.get_groups_by_ids.return_value = enrich_results or []
    return client


# ───────── _build_search_plan ─────────


def test_search_plan_starts_with_geo_only_query():
    plan = _build_search_plan("Малмыж", ["novost"])
    assert plan[0] == ("Малмыж", "geo_search")
    # Каждый keyword рождает свой kw:... source
    sources = [s for _, s in plan[1:]]
    assert all(s.startswith("kw:") for s in sources)


def test_search_plan_deduplicates_keywords_across_categories():
    # 'инфо' встречается только в novost; 'афиша' — в kultura — никаких дублей.
    plan = _build_search_plan("Киров", ["novost", "kultura"])
    queries = [q for q, _ in plan]
    assert len(queries) == len(set(queries))  # все уникальны


def test_search_plan_no_categories_returns_only_geo():
    plan = _build_search_plan("Киров", [])
    assert plan == [("Киров", "geo_search")]


# ───────── discover_for_region ─────────


def test_discover_empty_city_returns_empty():
    client = MagicMock()
    assert discover_for_region(client=client, center_city="  ") == []
    client.search_groups.assert_not_called()


def test_discover_calls_search_for_geo_and_each_keyword():
    client = MagicMock()
    client.search_groups.return_value = []
    client.get_groups_by_ids.return_value = []

    discover_for_region(
        client=client,
        center_city="Малмыж",
        categories=["novost", "sport"],
    )
    # 1 geo + 3 (novost: новости, инфо, вести) + 2 (sport: спорт, фитнес) = 6
    expected_kw = len(CATEGORY_KEYWORDS["novost"]) + len(CATEGORY_KEYWORDS["sport"])
    assert client.search_groups.call_count == 1 + expected_kw


def test_discover_passes_city_id_only_for_geo_search():
    """Geo-search использует city_id; keyword-searches — нет."""
    client = MagicMock()
    client.search_groups.return_value = []
    client.get_groups_by_ids.return_value = []

    discover_for_region(
        client=client,
        center_city="Малмыж",
        vk_city_id=314,
        categories=["novost"],
    )
    calls = client.search_groups.call_args_list
    # First call — geo, should have city_id=314
    assert calls[0].kwargs["city_id"] == 314
    # Subsequent calls — keyword, city_id must be None
    for c in calls[1:]:
        assert c.kwargs["city_id"] is None


def test_discover_dedup_by_vk_id_first_source_wins():
    """Same group appears via geo_search and kw:новости — discovered_via = geo_search."""
    client = MagicMock()

    def search_side_effect(*, query, city_id=None, count=100, offset=0):
        if query == "Малмыж":
            return [{"id": 1, "name": "Group via geo"}]
        if "новости" in query:
            return [{"id": 1, "name": "Group via kw"}]
        return []

    client.search_groups.side_effect = search_side_effect
    client.get_groups_by_ids.return_value = []

    groups = discover_for_region(
        client=client,
        center_city="Малмыж",
        categories=["novost"],
    )
    assert len(groups) == 1
    assert groups[0].vk_id == 1
    assert groups[0].discovered_via == "geo_search"
    assert groups[0].name == "Group via geo"


def test_discover_filters_out_excluded_vk_ids():
    client = MagicMock()
    client.search_groups.return_value = [
        {"id": 10, "name": "Already added"},
        {"id": 20, "name": "New one"},
    ]
    client.get_groups_by_ids.return_value = []

    groups = discover_for_region(
        client=client,
        center_city="X",
        categories=[],
        exclude_vk_ids=[10],
    )
    assert [g.vk_id for g in groups] == [20]


def test_discover_handles_negative_vk_id_in_exclude_via_abs():
    client = MagicMock()
    client.search_groups.return_value = [{"id": 10, "name": "A"}]
    client.get_groups_by_ids.return_value = []

    groups = discover_for_region(
        client=client,
        center_city="X",
        categories=[],
        exclude_vk_ids=[-10],  # negative — should still match abs(10)
    )
    assert groups == []


def test_discover_enriches_from_groups_get_by_id_once():
    """get_groups_by_ids зовётся ровно один раз для всех уникальных id."""
    client = MagicMock()
    client.search_groups.side_effect = [
        [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}],
        [{"id": 3, "name": "C"}],
    ]
    client.get_groups_by_ids.return_value = [
        {"id": 1, "name": "A", "description": "desc-1", "members_count": 5000},
        {"id": 2, "name": "B", "members_count": 100},
    ]

    groups = discover_for_region(
        client=client,
        center_city="X",
        categories=["novost"],  # 3 keyword searches + 1 geo = 4 search calls
    )
    # get_groups_by_ids — ровно один вызов с тремя id.
    assert client.get_groups_by_ids.call_count == 1
    ids_arg = client.get_groups_by_ids.call_args[0][0]
    assert sorted(ids_arg) == [1, 2, 3]

    # Enrichment должен заполнить description + members_count для id=1.
    by_id = {g.vk_id: g for g in groups}
    assert by_id[1].description == "desc-1"
    assert by_id[1].members_count == 5000
    assert by_id[2].members_count == 100


def test_discover_skips_items_with_zero_id():
    client = MagicMock()
    client.search_groups.return_value = [{"id": 0, "name": "Bad"}, {"id": 1, "name": "Good"}]
    client.get_groups_by_ids.return_value = []
    groups = discover_for_region(client=client, center_city="X", categories=[])
    assert [g.vk_id for g in groups] == [1]
