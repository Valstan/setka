"""Сторож на источник VK-токена в discovery.

Инцидент 2026-08-31. На странице `/tokens` висела строка
``UNKNOWN:d990230a | чтение | 1475 запросов | wall.get 1475`` — расход мимо
маршрутизатора. Цепочка: ``_pick_parse_token`` звал sync-мост
``get_active_parse_tokens_sync``, но все три его вызова живут внутри
``async def``; мост падал на крутящейся петле, роутер глотал исключение
``except Exception`` и возвращал токены **из env**, минуя ``_register_name_safe``.

Последствий два, и второе дороже первого:

1. учёт врал — расход не привязывался к имени токена;
2. подменялся источник истины: env вместо БД. Ровно тот рассинхрон, против
   которого роутер и писался после инцидента VALSTAN 2026-05-28 (токен
   ротировали через UI, env остался старым).

Поэтому проверяем не «функция что-то вернула», а откуда именно.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import modules.vk_token_router as router
from tasks import discovery_tasks as dt


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False


def _session_factory() -> MagicMock:
    factory = MagicMock()
    factory.return_value = _FakeSession()
    return factory


async def test_pick_parse_token_reads_through_router() -> None:
    """Токен приходит из async-функции роутера (источник — БД)."""
    fake = AsyncMock(return_value={"VALSTAN": "tok-from-db"})

    with (
        patch.object(router, "get_active_parse_tokens", fake),
        patch.object(dt, "AsyncSessionLocal", _session_factory()),
    ):
        token = await dt._pick_parse_token()

    assert token == "tok-from-db"
    fake.assert_awaited_once()


async def test_pick_parse_token_never_uses_sync_bridge() -> None:
    """Регресс: sync-мост из async-контекста не зовётся вовсе.

    Это и есть суть починки. Тест на возвращаемое значение остался бы
    зелёным и на сломанном коде — env-fallback тоже отдавал строку токена.
    """
    bridge = MagicMock(return_value={"VALSTAN": "tok-from-env"})

    with (
        patch.object(router, "get_active_parse_tokens", AsyncMock(return_value={"MAMA": "tok"})),
        patch.object(router, "get_active_parse_tokens_sync", bridge),
        patch.object(dt, "AsyncSessionLocal", _session_factory()),
    ):
        await dt._pick_parse_token()

    bridge.assert_not_called()


async def test_pick_parse_token_skips_empty_values() -> None:
    """Пустая строка токена — не токен; берём следующий непустой."""
    with (
        patch.object(
            router,
            "get_active_parse_tokens",
            AsyncMock(return_value={"VITA": "", "VALSTAN": "tok"}),
        ),
        patch.object(dt, "AsyncSessionLocal", _session_factory()),
    ):
        assert await dt._pick_parse_token() == "tok"


async def test_pick_parse_token_returns_none_when_router_empty() -> None:
    """Живых READ-токенов нет — None, caller обязан вернуть внятную ошибку."""
    with (
        patch.object(router, "get_active_parse_tokens", AsyncMock(return_value={})),
        patch.object(dt, "AsyncSessionLocal", _session_factory()),
    ):
        assert await dt._pick_parse_token() is None
