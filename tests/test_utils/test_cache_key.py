"""Ключ кэша по умолчанию: стабилен при DI-аргументах, чувствителен к примитивам.

Регрессия на граблю #217: девять @cache-эндпоинтов строили ключ из ВСЕХ
аргументов, включая ``db: AsyncSession``; str() сессии содержит адрес объекта,
разный на каждый запрос, — ключ не повторялся никогда, кэш промахивался всегда.
"""

from utils.cache import _stable_repr, build_default_cache_key


class _FakeSession:
    """str() даёт адрес в памяти — как у AsyncSession/Request."""


class TestStableRepr:
    def test_primitives_pass(self):
        assert _stable_repr("mi") == "'mi'"
        assert _stable_repr(42) == "42"
        assert _stable_repr(3.5) == "3.5"
        assert _stable_repr(True) == "True"
        assert _stable_repr(None) == "None"

    def test_di_objects_excluded(self):
        assert _stable_repr(_FakeSession()) is None
        assert _stable_repr(object()) is None

    def test_collections_of_primitives_pass(self):
        assert _stable_repr([1, "a"]) is not None
        assert _stable_repr({"k": 1}) is not None
        assert _stable_repr((1, 2)) is not None

    def test_collection_with_unstable_element_excluded(self):
        assert _stable_repr([1, _FakeSession()]) is None
        assert _stable_repr({"k": _FakeSession()}) is None

    def test_set_order_independent(self):
        assert _stable_repr({3, 1, 2}) == _stable_repr({2, 3, 1})

    def test_dict_order_independent(self):
        assert _stable_repr({"a": 1, "b": 2}) == _stable_repr({"b": 2, "a": 1})


class TestBuildDefaultCacheKey:
    def test_same_key_across_different_sessions(self):
        """Суть бага: разные экземпляры сессии не должны менять ключ."""
        k1 = build_default_cache_key("get_posts", "posts", (), {"region_id": 5, "db": _FakeSession()})
        k2 = build_default_cache_key("get_posts", "posts", (), {"region_id": 5, "db": _FakeSession()})
        assert k1 == k2

    def test_primitive_change_changes_key(self):
        k1 = build_default_cache_key("get_posts", "posts", (), {"region_id": 5, "db": _FakeSession()})
        k2 = build_default_cache_key("get_posts", "posts", (), {"region_id": 6, "db": _FakeSession()})
        assert k1 != k2

    def test_kwarg_name_matters(self):
        k1 = build_default_cache_key("f", "", (), {"skip": 1})
        k2 = build_default_cache_key("f", "", (), {"limit": 1})
        assert k1 != k2

    def test_positional_position_matters(self):
        k1 = build_default_cache_key("f", "", ("a", "b"), {})
        k2 = build_default_cache_key("f", "", ("b", "a"), {})
        assert k1 != k2

    def test_prefix_in_key(self):
        assert build_default_cache_key("f", "posts", (), {}).startswith("posts:f:")
        assert build_default_cache_key("f", "", (), {}).startswith("f:")

    def test_str_vs_int_not_conflated(self):
        k1 = build_default_cache_key("f", "", (), {"x": 1})
        k2 = build_default_cache_key("f", "", (), {"x": "1"})
        assert k1 != k2
