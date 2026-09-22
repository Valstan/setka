"""Клиент ВК и кэш стен: попадание не должно стоить вызова к ВК.

Проверяется поведение врезки в боевом клиенте, а не сам кэш (он — в
``test_wall_cache.py``): что попадание не тратит токен и не ждёт тормоза
rate-limit, что пагинация кэш обходит, что пакетное чтение ходит в ВК только
за промахами и что отказ API в кэш не попадает.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
import vk_api

from modules.vk_monitor import wall_cache as wc
from modules.vk_monitor.vk_client import VKClient


class _FakeRedis:
    def __init__(self):
        self.store = {}
        self.ttls = {}

    def setex(self, key, ttl, value):
        self.store[key] = str(value)
        self.ttls[key] = ttl

    def get(self, key):
        return self.store.get(key)

    def delete(self, key):
        self.store.pop(key, None)
        self.ttls.pop(key, None)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(wc, "_redis_client", fake)
    monkeypatch.setattr(wc, "_redis_pid", os.getpid())
    monkeypatch.delenv("WALL_CACHE_TTL_SECONDS", raising=False)
    monkeypatch.delenv("WALL_HISTORY_CACHE_TTL_SECONDS", raising=False)
    # Тормоз per-token — общий синглтон; в тестах он не нужен и только тянет время.
    VKClient._rate_limiter = None
    yield fake
    VKClient._rate_limiter = None


def _make_client():
    with patch("modules.vk_monitor.vk_client.vk_api.VkApi") as m:
        instance = MagicMock()
        instance.get_api.return_value = MagicMock(name="api")
        m.return_value = instance
        return VKClient("token")


def _posts(n, owner=-1):
    return [{"id": i, "owner_id": owner, "text": f"п{i}"} for i in range(n)]


def test_hit_does_not_touch_vk_at_all():
    client = _make_client()
    wc.store_wall(-1, 20, _posts(20))

    got = client.get_wall_posts(-1, 20)

    assert len(got) == 20
    client.vk.wall.get.assert_not_called()
    assert client.wall_cache_stats.hits == 1


def test_miss_fetches_and_then_stores(_env):
    client = _make_client()
    client.vk.wall.get.return_value = {"items": _posts(20)}

    first = client.get_wall_posts(-1, 20)
    second = client.get_wall_posts(-1, 20)

    assert len(first) == len(second) == 20
    client.vk.wall.get.assert_called_once()
    assert wc._key(-1) in _env.store


def test_pagination_bypasses_the_cache():
    """offset > 0 читает другой кусок стены, а ключ здесь один на владельца."""
    client = _make_client()
    wc.store_wall(-1, 20, _posts(20))
    client.vk.wall.get.return_value = {"items": _posts(5)}

    client.get_wall_posts(-1, 20, offset=20)

    client.vk.wall.get.assert_called_once()
    assert client.wall_cache_stats.bypass == 1


def test_explicit_zero_ttl_bypasses_the_cache():
    """Вызывающий, которому нужны свежие счётчики, обходит кэш явно."""
    client = _make_client()
    wc.store_wall(-1, 20, _posts(20))
    client.vk.wall.get.return_value = {"items": _posts(20)}

    client.get_wall_posts(-1, 20, cache_ttl=0)

    client.vk.wall.get.assert_called_once()


def test_api_error_result_is_not_cached(_env):
    """Пустой ответ от ошибки ВК не должен превращаться в «постов нет» на 5 минут."""
    client = _make_client()
    client.vk.wall.get.side_effect = vk_api.exceptions.ApiError(
        MagicMock(), "wall.get", {}, False, {"error_code": 15, "error_msg": "Access denied"}
    )

    assert client.get_wall_posts(-1, 20) == []
    assert _env.store == {}


def test_batch_asks_vk_only_for_misses():
    client = _make_client()
    wc.store_wall(-1, 20, _posts(20, owner=-1))
    client.session.method.return_value = [{"items": _posts(20, owner=-2)}]

    out = client.get_wall_posts_batch([-1, -2], count=20)

    assert len(out[-1]) == 20 and len(out[-2]) == 20
    code = client.session.method.call_args[0][1]["code"]
    assert "-2" in code
    assert "-1," not in code and "[-1]" not in code


def test_batch_with_everything_cached_makes_no_request():
    client = _make_client()
    wc.store_wall(-1, 20, _posts(20, owner=-1))
    wc.store_wall(-2, 20, _posts(20, owner=-2))

    out = client.get_wall_posts_batch([-1, -2], count=20)

    client.session.method.assert_not_called()
    assert len(out[-1]) == 20 and len(out[-2]) == 20


def test_batch_stores_what_it_fetched(_env):
    client = _make_client()
    client.session.method.return_value = [{"items": _posts(20, owner=-7)}]

    client.get_wall_posts_batch([-7], count=20)

    assert wc._key(-7) in _env.store
