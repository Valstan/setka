"""HTTP к Bot API Telegram с повторами на сетевой отказ — одна дверь для всех отправок.

Замер Матрицы 04.09 с трёх точек (G307, D-076): ``api.telegram.org`` с боксов
хостера **не заблокирован, а теряет около половины SYN** ко всем DC Telegram
(13/20 с нашего бокса, контроль ``1.1.1.1`` — 20/20); установленное соединение
живёт. Наши отправки шли одним ``requests.post(..., timeout=15)`` без повторов —
каждый второй запрос молча падал ``ConnectTimeout``'ом, и никто их не считал.
В логах воркера за 01–03.09 это 45–59 сетевых отказов Telegram в сутки.

Лечение то же, что у Матрицы (#758): **короткий таймаут на попытку и до шести
повторов — только на сетевой отказ**. Ответ HTTP (4xx/5xx, 429) не повторяем: он
доказывает, что соединение установилось, а повтор ``sendMessage`` по таймауту
чтения задвоил бы сообщение. Поэтому ловим ``ConnectionError`` (в него входит и
``ConnectTimeout``), но **не** ``ReadTimeout``.

Все вызовы идут через ``requests.post``/``requests.get``, причём модуль берётся
**в момент вызова** (локальный импорт): тесты проекта патчат ``requests.post``
глобально или подменяют ``sys.modules["requests"]`` целиком, и с этим помощником
оба приёма продолжают работать без правок. Классы исключений — из настоящего
``requests``, привязаны при импорте: подменённый модуль их не обязан иметь.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

import requests as _real_requests

logger = logging.getLogger(__name__)


def _requests():
    """Модуль ``requests`` на момент вызова (см. докстринг про тесты)."""
    import requests

    return requests


# Таймаут одной попытки: при потере SYN ждать 15 с бессмысленно — повтор быстрее.
ATTEMPT_TIMEOUT = 6.0
# Шесть попыток при p(потери)≈0.5 дают p(все упали)≈1.5% вместо 50%.
ATTEMPTS = 6
# Пауза между попытками — линейная и короткая: это не rate-limit, а лотерея SYN.
_BACKOFF_STEP = 0.5

# То, что считается «сети нет»: ConnectionError покрывает и ConnectTimeout
# (он наследует от обоих), ReadTimeout сюда намеренно не входит.
NETWORK_ERRORS = (_real_requests.exceptions.ConnectionError,)


def _retrying(
    do: Callable[[float], Any],
    *,
    what: str,
    attempts: int,
    timeout: float,
    sleep: Optional[Callable[[float], None]],
) -> Any:
    tries = max(1, attempts)
    last: Optional[BaseException] = None
    for i in range(tries):
        try:
            return do(timeout)
        except NETWORK_ERRORS as e:
            last = e
            if i + 1 < tries:
                logger.debug("telegram %s: сеть (попытка %d/%d): %s", what, i + 1, tries, e)
                if sleep is not None:
                    sleep(_BACKOFF_STEP * (i + 1))
    assert last is not None
    logger.warning("telegram %s: сеть не ответила за %d попыток: %s", what, tries, last)
    raise last


def post(
    url: str,
    *,
    attempts: int = ATTEMPTS,
    timeout: float = ATTEMPT_TIMEOUT,
    sleep: Optional[Callable[[float], None]] = time.sleep,
    **kw: Any,
) -> Any:
    """``requests.post`` с повторами на сетевой отказ. Исключение — после всех попыток."""
    return _retrying(
        lambda t: _requests().post(url, timeout=t, **kw),
        what="POST",
        attempts=attempts,
        timeout=timeout,
        sleep=sleep,
    )


def get(
    url: str,
    *,
    attempts: int = ATTEMPTS,
    timeout: float = ATTEMPT_TIMEOUT,
    sleep: Optional[Callable[[float], None]] = time.sleep,
    **kw: Any,
) -> Any:
    """``requests.get`` с повторами на сетевой отказ."""
    return _retrying(
        lambda t: _requests().get(url, timeout=t, **kw),
        what="GET",
        attempts=attempts,
        timeout=timeout,
        sleep=sleep,
    )


async def apost(url: str, *, attempts: int = ATTEMPTS, timeout: float = ATTEMPT_TIMEOUT, **kw: Any):
    """Асинхронный POST через httpx с теми же правилами повтора."""
    import asyncio

    import httpx

    tries = max(1, attempts)
    last: Optional[BaseException] = None
    for i in range(tries):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                return await client.post(url, **kw)
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            last = e
            if i + 1 < tries:
                await asyncio.sleep(_BACKOFF_STEP * (i + 1))
    assert last is not None
    logger.warning("telegram POST: сеть не ответила за %d попыток: %s", tries, last)
    raise last


def get_httpx(url: str, *, attempts: int = ATTEMPTS, timeout: float = ATTEMPT_TIMEOUT, **kw: Any):
    """Синхронный GET через httpx (для мест, где уже httpx) — повторы те же."""
    import httpx

    tries = max(1, attempts)
    last: Optional[BaseException] = None
    for i in range(tries):
        try:
            return httpx.get(url, timeout=timeout, **kw)
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            last = e
            if i + 1 < tries:
                time.sleep(_BACKOFF_STEP * (i + 1))
    assert last is not None
    logger.warning("telegram GET: сеть не ответила за %d попыток: %s", tries, last)
    raise last
