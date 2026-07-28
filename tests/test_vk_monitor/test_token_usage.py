"""Tests for :mod:`modules.vk_monitor.token_usage` — учёт расхода VK-запросов.

Redis в тестах не поднимается, поэтому модуль работает на memory-backend
(``_get_redis`` возвращает ``None``) — это тот же путь, что на проде при
недоступном Redis, и он обязан быть рабочим: балансировка деградирует, сбор
не останавливается.
"""

import os
from datetime import timedelta
from unittest.mock import patch

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

from modules.vk_monitor import token_usage  # noqa: E402
from utils.timezone import now_moscow  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_usage():
    """Каждый тест начинает с чистого реестра и памяти."""
    token_usage.reset_for_tests()
    with patch.object(token_usage, "_get_redis", return_value=None):
        yield
    token_usage.reset_for_tests()


class TestNameRegistry:
    def test_registered_token_resolves_to_name(self):
        token_usage.register_token_name("tok_secret", "mama")
        assert token_usage.resolve_token_name("tok_secret") == "MAMA"

    def test_unregistered_token_gets_stable_fingerprint(self):
        """Незнакомый токен = расход мимо роутера; отпечаток стабилен."""
        first = token_usage.resolve_token_name("tok_unknown")
        second = token_usage.resolve_token_name("tok_unknown")
        assert first.startswith("UNKNOWN:")
        assert first == second
        # Разные токены — разные отпечатки, значение токена не раскрывается.
        assert token_usage.resolve_token_name("tok_other") != first
        assert "tok_unknown" not in first

    def test_empty_token_and_name_ignored(self):
        token_usage.register_token_name("", "MAMA")
        token_usage.register_token_name("tok", "")
        assert token_usage.resolve_token_name("tok").startswith("UNKNOWN:")
        assert token_usage.resolve_token_name("") == "UNKNOWN:empty"


class TestRecordCall:
    def test_counts_total_and_per_method(self):
        token_usage.register_token_name("tok_v", "VITA")
        token_usage.record_call("tok_v", "wall.get")
        token_usage.record_call("tok_v", "wall.get")
        token_usage.record_call("tok_v", "groups.search")

        usage = token_usage.get_usage()
        assert usage["VITA"]["total"] == 3
        assert usage["VITA"]["m:wall.get"] == 2
        assert usage["VITA"]["m:groups.search"] == 1

    def test_calls_today_is_flat_map(self):
        token_usage.register_token_name("tok_m", "MAMA")
        token_usage.register_token_name("tok_v", "VALSTAN")
        for _ in range(5):
            token_usage.record_call("tok_m", "wall.get")
        token_usage.record_call("tok_v", "wall.get")

        assert token_usage.get_calls_today() == {"MAMA": 5, "VALSTAN": 1}

    def test_method_optional(self):
        token_usage.register_token_name("tok_v", "VITA")
        token_usage.record_call("tok_v")
        usage = token_usage.get_usage()
        assert usage["VITA"] == {"total": 1}

    def test_never_raises_on_broken_backend(self):
        """Учёт в горячем пути парсинга не имеет права ронять сбор."""
        with patch.object(token_usage, "_get_redis", side_effect=RuntimeError("boom")):
            token_usage.record_call("tok_v", "wall.get")  # не должно бросить

    def test_unknown_token_still_counted(self):
        """Расход мимо роутера виден в отчёте отдельной строкой."""
        token_usage.record_call("tok_stray", "wall.get")
        usage = token_usage.get_usage()
        name = next(iter(usage))
        assert name.startswith("UNKNOWN:")
        assert usage[name]["total"] == 1


class TestUsageWindow:
    def test_window_covers_requested_days(self):
        token_usage.register_token_name("tok_v", "VITA")
        token_usage.record_call("tok_v", "wall.get")

        window = token_usage.get_usage_window(3)
        # Сутки берём МОСКОВСКИЕ — тем же способом, что и сам модуль
        # (``_today_moscow``). Раньше здесь стоял ``date.today()``, то есть дата
        # в таймзоне процесса, и с 21:00 до 24:00 UTC тест разъезжался с
        # продуктовым кодом на сутки: запись легла в московское «завтра», а
        # проверка искала UTC-«сегодня» → KeyError. Поймано красным прогоном CI
        # в 21:01 UTC (= 00:01 MSK) при ревизии гейтов #104; локально в MSK
        # такой прогон зелёный круглосуточно, поэтому мина и дожила до сюда.
        # Продуктовое поведение верное и намеренное (см.
        # tests/test_monitoring/test_token_usage_day_boundary.py) — правится тест.
        today = now_moscow().date()
        assert len(window) == 3
        assert window[today.isoformat()]["VITA"]["total"] == 1
        # Вчерашний день пуст — записей не было.
        yesterday = (today - timedelta(days=1)).isoformat()
        assert window[yesterday] == {}

    def test_window_minimum_one_day(self):
        assert len(token_usage.get_usage_window(0)) == 1
