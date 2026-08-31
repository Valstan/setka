"""
Async runner helpers for Celery workers.

Why this exists:
- Many Celery tasks in SETKA are synchronous but call async code
  (SQLAlchemy async, VK async clients, etc.).
- Using `asyncio.run()` inside Celery tasks creates a NEW event loop every call.
- SQLAlchemy asyncpg connections (and the async engine pool) are bound to the
  loop they were created in. Reusing pooled connections across different loops
  leads to errors like: "got Future attached to a different loop" /
  "Event loop is closed".

Solution:
- Keep ONE event loop per worker process and reuse it for all coroutine executions.
"""

from __future__ import annotations

import asyncio
from typing import Any, Coroutine, Optional, TypeVar

T = TypeVar("T")

_loop: Optional[asyncio.AbstractEventLoop] = None


def run_coro(coro: Coroutine[Any, Any, T]) -> T:
    """
    Run a coroutine on a persistent event loop (per process).

    NOTE:
    - This function is intended to be used from synchronous Celery tasks.
    - Celery prefork workers execute tasks sequentially per process, so loop reuse is safe here.
    """
    global _loop

    # If we're already inside an event loop, we cannot "sync-wait" safely.
    #
    # ⚠️ `raise` обязан жить в `else`, а не в теле `try`. Раньше он стоял внутри
    # `try` и попадал в собственный `except RuntimeError` строкой ниже — то есть
    # сторож глушил сам себя и никогда не срабатывал. Отказ всё равно наступал,
    # но ниже и с чужим текстом («This event loop is already running» из
    # `run_until_complete`), поэтому вызывающий код ловил его как «непонятную
    # ошибку БД» и уходил в свой fallback. Именно так discovery годами брал
    # VK-токены из env мимо маршрутизатора (расход уезжал в отчёт `/tokens`
    # строкой `UNKNOWN:<fp>`, найдено 2026-08-31).
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # No running loop in this thread => OK.
        pass
    else:
        raise RuntimeError("run_coro() cannot be called from within a running event loop")

    if _loop is None or _loop.is_closed():
        _loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_loop)

    return _loop.run_until_complete(coro)
