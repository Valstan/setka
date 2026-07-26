"""Граница суток у счётчиков расхода — московская, а не машинная.

Счётчики расхода посуточные, и балансировка чтения смотрит на «сколько токен
потратил сегодня». Значит важно, когда наступает «сегодня»: раньше день брался
из ``date.today()``, то есть из таймзоны процесса. На проде машина стоит в MSK,
поэтому ответ был верным **случайно** — переезд в UTC сдвинул бы обнуление на
03:00 MSK и три часа каждую ночь балансировка работала бы по вчерашним числам.

Тест фиксирует явную привязку к Москве (владелец 2026-07-26: «обнулять каждые
сутки в 12 часов ночи»).
"""

from __future__ import annotations

import os
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

from modules.vk_monitor import token_usage  # noqa: E402

MOSCOW = ZoneInfo("Europe/Moscow")


def test_day_key_follows_moscow_date():
    """00:30 МСК — уже новые сутки, хотя в UTC ещё вчера."""
    moment = datetime(2026, 7, 27, 0, 30, tzinfo=MOSCOW)
    with patch("utils.timezone.now_moscow", return_value=moment):
        assert token_usage._day_key() == "2026-07-27"


def test_day_key_before_midnight_is_previous_day():
    moment = datetime(2026, 7, 26, 23, 45, tzinfo=MOSCOW)
    with patch("utils.timezone.now_moscow", return_value=moment):
        assert token_usage._day_key() == "2026-07-26"


def test_explicit_day_wins():
    """Явная дата (окно отчёта) не подменяется текущей."""
    from datetime import date

    assert token_usage._day_key(date(2026, 1, 2)) == "2026-01-02"
