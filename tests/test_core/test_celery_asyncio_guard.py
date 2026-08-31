"""Сторож на защиту ``run_coro`` от вызова из работающего event-loop.

Почему этот файл существует. Защита была написана так:

    try:
        asyncio.get_running_loop()
        raise RuntimeError("run_coro() cannot be called from within ...")
    except RuntimeError:
        pass

— собственный ``raise`` попадал в собственный ``except`` строкой ниже, и
сторож глушил сам себя. Отказ всё равно наступал, но ниже и с ЧУЖИМ текстом
(«This event loop is already running» из ``run_until_complete``), поэтому
вызывающий код читал его как «непонятная ошибка» и уходил в свой fallback.
Именно так ``tasks/discovery_tasks`` годами брал VK-токены из env мимо
маршрутизатора (расход уезжал в отчёт `/tokens` строкой ``UNKNOWN:<fp>``,
найдено 2026-08-31).

Отсюда форма проверки: мало убедиться, что вызов падает — он падал и до
починки. Проверяем ИМЕННО ТЕКСТ, потому что разница между двумя отказами и
была ценой дефекта.
"""

from __future__ import annotations

import asyncio

import pytest

from utils.celery_asyncio import run_coro

_GUARD_MESSAGE = "cannot be called from within a running event loop"


def test_run_coro_runs_coroutine_outside_loop() -> None:
    """Штатный путь (синхронная Celery-таска) не сломан починкой."""

    async def _answer() -> int:
        return 42

    assert run_coro(_answer()) == 42


def test_run_coro_reuses_one_loop() -> None:
    """Петля переиспользуется — ради этого модуль и написан."""

    async def _loop_id() -> int:
        return id(asyncio.get_running_loop())

    assert run_coro(_loop_id()) == run_coro(_loop_id())


async def test_run_coro_refuses_inside_running_loop() -> None:
    """Из async-контекста — отказ СВОИМ сообщением, а не чужим.

    До починки здесь поднималось ``RuntimeError`` из ``run_until_complete``
    («This event loop is already running» / «Cannot run the event loop while
    another loop is running»), то есть тест на факт исключения был бы зелёным
    и на сломанном сторо́же. Регресс ловит только сверка текста.
    """

    async def _noop() -> None:
        return None

    coro = _noop()
    try:
        with pytest.raises(RuntimeError, match=_GUARD_MESSAGE):
            run_coro(coro)
    finally:
        coro.close()
