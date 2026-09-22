"""Кэш стен ВК: одна стена читается один раз (modules/vk_monitor/wall_cache).

Redis подменён in-memory фейком, сети нет. Проверяется не «работает ли
сохранение», а свойства, поломка которых будет тихой: что запись под больший
count обслуживает меньший запрос, что пустой ответ не кэшируется, что нулевой
TTL выключает слой целиком и что любая беда с Redis — промах, а не исключение.
"""

from __future__ import annotations

import os

import pytest

from modules.vk_monitor import wall_cache as wc


class _FakeRedis:
    """Минимальный in-memory Redis: setex/get/delete с decode_responses."""

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


class _BrokenRedis:
    def get(self, key):
        raise RuntimeError("redis упал")

    def setex(self, key, ttl, value):
        raise RuntimeError("redis упал")

    def delete(self, key):
        raise RuntimeError("redis упал")


@pytest.fixture(autouse=True)
def _fake_redis(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(wc, "_redis_client", fake)
    # PID-guard: помечаем клиент «своим», иначе _redis() сочтёт его
    # унаследованным после форка и пересоздаст.
    monkeypatch.setattr(wc, "_redis_pid", os.getpid())
    monkeypatch.delenv("WALL_CACHE_TTL_SECONDS", raising=False)
    monkeypatch.delenv("WALL_HISTORY_CACHE_TTL_SECONDS", raising=False)
    return fake


def _posts(n):
    return [{"id": i, "owner_id": -1, "text": f"пост {i}"} for i in range(n)]


def test_roundtrip_returns_what_was_stored(_fake_redis):
    wc.store_wall(-1, 20, _posts(20))

    got = wc.get_cached_wall(-1, 20)

    assert got is not None and len(got) == 20
    assert got[0]["text"] == "пост 0"
    assert _fake_redis.ttls[wc._key(-1)] == 300


def test_bigger_stored_count_serves_a_smaller_request():
    """Сто постов собственной стены закрывают и чтение двадцати каскадом.

    Ради этого свойства кэш и ключуется по владельцу, а не по паре
    (владелец, count): иначе каждая подсистема со своим count имела бы
    собственную запись, и повтор никуда бы не делся.
    """
    wc.store_wall(-1, 100, _posts(100))

    got = wc.get_cached_wall(-1, 20)

    assert got is not None and len(got) == 20


def test_smaller_stored_count_is_a_miss():
    """Под больший запрос в кэше просто нет нужных постов — это промах."""
    wc.store_wall(-1, 20, _posts(20))

    assert wc.get_cached_wall(-1, 100) is None


def test_zero_ttl_disables_the_layer_completely(monkeypatch):
    """Откат без деплоя: одна переменная окружения выключает кэш."""
    monkeypatch.setenv("WALL_CACHE_TTL_SECONDS", "0")
    monkeypatch.setenv("WALL_HISTORY_CACHE_TTL_SECONDS", "0")

    wc.store_wall(-1, 20, _posts(20))

    assert wc.get_cached_wall(-1, 20) is None


def test_empty_wall_is_not_cached(_fake_redis):
    """Пустая стена — обычно временный отказ ВК, а не факт про стену.

    Запомнить его значило бы превратить одну неудачу в серию: следующие
    подсистемы получили бы «постов нет» уже из кэша, не сходив в ВК.
    """
    wc.store_wall(-1, 20, [])

    assert _fake_redis.store == {}
    assert wc.get_cached_wall(-1, 20) is None


def test_invalidate_forgets_the_wall():
    """Сброс после публикации: следующая тема обязана увидеть свежую сводку."""
    wc.store_wall(-1, 100, _posts(100))

    wc.invalidate_wall(-1)

    assert wc.get_cached_wall(-1, 100) is None


def test_history_ttl_is_used_when_asked(_fake_redis):
    wc.store_wall(-1, 100, _posts(100), ttl=wc.wall_history_ttl_seconds())

    assert _fake_redis.ttls[wc._key(-1)] == 3600


def test_broken_redis_is_a_miss_not_an_exception(monkeypatch):
    """Кэш обязан уметь исчезнуть бесследно: беда с Redis — промах, не отказ."""
    monkeypatch.setattr(wc, "_redis_client", _BrokenRedis())
    monkeypatch.setattr(wc, "_redis_pid", os.getpid())

    assert wc.get_cached_wall(-1, 20) is None
    wc.store_wall(-1, 20, _posts(20))  # не бросает
    wc.invalidate_wall(-1)  # не бросает


def test_corrupted_payload_is_a_miss(_fake_redis):
    """Битый ключ (чужая запись, оборванный json) не должен ронять волну."""
    _fake_redis.store[wc._key(-1)] = "{это не json"

    assert wc.get_cached_wall(-1, 20) is None


def test_ttl_env_garbage_falls_back_to_defaults(monkeypatch):
    monkeypatch.setenv("WALL_CACHE_TTL_SECONDS", "пять минут")
    monkeypatch.setenv("WALL_HISTORY_CACHE_TTL_SECONDS", "час")

    assert wc.wall_cache_ttl_seconds() == 300
    assert wc.wall_history_ttl_seconds() == 3600
