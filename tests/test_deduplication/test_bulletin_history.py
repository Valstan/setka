"""Unit tests for digest history dedup helpers."""

from types import SimpleNamespace

from modules.deduplication.bulletin_history import (
    append_unique_limited,
    build_region_dedup_sets,
    extract_source_lips_from_target_group_posts,
)


def test_build_region_dedup_sets_merges_all_themes():
    wt1 = SimpleNamespace(lip=["-1_1", "-1_2"], hash=["a", "b"])
    wt2 = SimpleNamespace(lip=["-2_3"], hash=["c"])
    lips, hashes = build_region_dedup_sets([wt1, wt2])
    assert lips == {"-1_1", "-1_2", "-2_3"}
    assert hashes == {"a", "b", "c"}


def test_extract_source_lips_from_target_group_posts_parses_wall_links():
    posts = [
        {"text": "✍ новость\n[https://vk.com/wall-10_11|Источник]"},
        {"text": "ещё ссылка wall-20_21 и дубликат wall-20_21"},
    ]
    lips = extract_source_lips_from_target_group_posts(posts)
    assert "10_11" in lips
    assert "20_21" in lips
    assert len(lips) == 2


def test_append_unique_limited_keeps_latest_unique_tail():
    existing = ["a", "b", "c"]
    out = append_unique_limited(existing, ["b", "d", "e"], limit=4)
    assert out == ["c", "b", "d", "e"]
