"""Per-token rate-limit для VK API calls.

Два backend'а с одинаковым интерфейсом:

- ``ThreadingRateLimiter`` — per-process через ``threading.Lock`` (default,
  безопасный fallback). Подходит для single-process Celery worker.
- ``RedisRateLimiter`` — cross-process через Redis Lua-script. Нужен для
  multi-process worker (``celery -c N`` с prefork): все процессы видят
  один общий счётчик per-token.

Выбор backend'а — через env ``VK_RATE_LIMIT_BACKEND`` (``threading`` |
``redis``). При ``redis``, но недоступном Redis, делается graceful
fallback на ``threading`` с логом WARNING — приоритет за «не повесить
систему», а не за «строгий cross-process контроль».

Токен в Redis не хранится сырым — ключ ``setka:vk_ratelimit:<sha256[:16]>``.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from typing import Dict, Optional, Protocol

logger = logging.getLogger(__name__)

# Ключевой префикс для Redis. Меняется только в тестах через monkey-patch.
REDIS_KEY_PREFIX = "setka:vk_ratelimit"


class RateLimiter(Protocol):
    """Контракт rate-limiter'а: блокирующий wait перед VK API вызовом."""

    def wait(self, token: str) -> None:
        """Block (sleep) пока не пройдёт ``interval`` с момента последнего
        вызова под тем же ``token``. Атомарно резервирует слот так, чтобы
        конкурирующий вызов увидел свежий timestamp и подождал свой interval.
        """
        ...


class ThreadingRateLimiter:
    """Per-process backend через ``threading.Lock``. Хранит state в памяти.

    Поведение совпадает с историческим in-class кодом VKClient до 2026-05-26.
    Cross-process НЕ синхронизирован — для multi-worker сценария бери Redis.
    """

    def __init__(self, interval: float) -> None:
        self.interval = float(interval)
        self._last_call_per_token: Dict[str, float] = {}
        self._per_token_locks: Dict[str, threading.Lock] = {}
        self._registry_lock = threading.Lock()

    def wait(self, token: str) -> None:
        with self._registry_lock:
            lock = self._per_token_locks.get(token)
            if lock is None:
                lock = threading.Lock()
                self._per_token_locks[token] = lock

        with lock:
            last = self._last_call_per_token.get(token, 0.0)
            now = time.monotonic()
            sleep_for = self.interval - (now - last)
            if sleep_for > 0:
                time.sleep(sleep_for)
            self._last_call_per_token[token] = time.monotonic()


# Lua-script под Redis: атомарно
#   1. читает last timestamp ms,
#   2. вычисляет wait_ms,
#   3. резервирует next slot = now + wait под TTL = interval*4 (чтобы ключ
#      сам выпал при простое — Redis не разрастался).
# Возвращает wait_ms (0 если ждать не надо).
_LUA_SCRIPT = """
local key = KEYS[1]
local now_ms = tonumber(ARGV[1])
local interval_ms = tonumber(ARGV[2])
local last = tonumber(redis.call('GET', key))
local wait_ms = 0
if last then
  local elapsed = now_ms - last
  if elapsed < interval_ms then
    wait_ms = interval_ms - elapsed
  end
end
redis.call('SET', key, now_ms + wait_ms, 'PX', interval_ms * 4)
return wait_ms
"""


class RedisRateLimiter:
    """Cross-process backend через Redis Lua-script.

    Использует синхронный ``redis.Redis`` клиент. Для async-кода вызывается
    через ``asyncio.to_thread(limiter.wait, token)`` — это уже делается в
    async-обёртках VKClient.
    """

    def __init__(self, redis_client, interval: float) -> None:
        self.client = redis_client
        self.interval = float(interval)
        self.interval_ms = int(self.interval * 1000)
        # register_script возвращает callable, который при вызове отправляет
        # EVALSHA → EVAL fallback (если script не закэширован в Redis).
        self._script = redis_client.register_script(_LUA_SCRIPT)

    def _key(self, token: str) -> str:
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
        return f"{REDIS_KEY_PREFIX}:{token_hash}"

    def wait(self, token: str) -> None:
        now_ms = int(time.time() * 1000)
        try:
            wait_ms = self._script(
                keys=[self._key(token)], args=[now_ms, self.interval_ms]
            )
        except Exception as e:
            # Redis недоступен в момент вызова (timeout, disconnect). Логируем
            # и не блокируем выполнение — это не критичная защита, а гигиена.
            # Cross-process гарантия временно теряется, но прод не падает.
            logger.warning("RedisRateLimiter Lua-call failed (token=…): %s", e)
            return
        wait_ms = int(wait_ms or 0)
        if wait_ms > 0:
            time.sleep(wait_ms / 1000.0)


def _build_redis_client():
    """Construct a sync ``redis.Redis`` from project REDIS config. Returns
    ``None`` если конфиг недоступен (тесты без env)."""
    try:
        import redis as redis_sync

        from config.runtime import REDIS
    except Exception as e:
        logger.debug("Redis client not available (config import failed): %s", e)
        return None

    try:
        client = redis_sync.Redis(
            host=REDIS["host"],
            port=int(REDIS["port"]),
            db=int(REDIS.get("db", 0)),
            socket_timeout=2.0,
            socket_connect_timeout=2.0,
        )
        client.ping()
        return client
    except Exception as e:
        logger.warning(
            "Redis ping failed, RedisRateLimiter unavailable — fallback to threading: %s",
            e,
        )
        return None


def build_rate_limiter(interval: float, backend: Optional[str] = None) -> RateLimiter:
    """Factory с graceful fallback.

    ``backend`` — explicit override (``"threading"`` | ``"redis"``). Если
    None, читается из env ``VK_RATE_LIMIT_BACKEND`` (default ``threading``).
    При ``redis`` без рабочего подключения — silent fallback на threading.
    """
    if backend is None:
        backend = os.getenv("VK_RATE_LIMIT_BACKEND", "threading").lower().strip()

    if backend == "redis":
        client = _build_redis_client()
        if client is not None:
            logger.info(
                "VKClient rate-limit backend: redis (interval=%.3fs)", interval
            )
            return RedisRateLimiter(client, interval)
        # fallback
        logger.warning("VKClient rate-limit: redis requested but unavailable — using threading")

    logger.info("VKClient rate-limit backend: threading (interval=%.3fs)", interval)
    return ThreadingRateLimiter(interval)
