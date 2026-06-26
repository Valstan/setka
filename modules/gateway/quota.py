"""Per-API-key квота для VK-шлюза (fixed-window через Redis).

Второй слой самозащиты — *на границе потребителя*, поверх защиты *на границе
токена* (per-token rate-limiter в ``VKClient`` + cooldown в ``TokenPolicy``).
Цель: один внешний проект не должен выесть общий VK-бюджет и затормозить
публикации/парсинг самого SARAFAN.

Окна — минута и сутки. Ключи Redis (без сырого секрета — только имя проекта):
    setka:gateway_quota:min:<KEY>:<epoch//60>
    setka:gateway_quota:day:<KEY>:<epoch//86400>

Redis недоступен → **fail-open** (пропускаем) + WARNING, как у
``RedisRateLimiter`` — приоритет «не повесить шлюз», защита токена остаётся.
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

REDIS_KEY_PREFIX = "setka:gateway_quota"

# Атомарный счётчик окна: INCR, при первом инкременте — EXPIRE на длину окна
# (чтобы ключ сам выпал и Redis не разрастался). Возвращает текущее значение.
_LUA_INCR = """
local v = redis.call('INCR', KEYS[1])
if v == 1 then
  redis.call('EXPIRE', KEYS[1], tonumber(ARGV[1]))
end
return v
"""


class GatewayQuota:
    """Проверка и расход квоты на один API-ключ.

    Создаётся per-request (дёшево). ``redis_client`` — sync ``redis.Redis``
    (см. :func:`modules.vk_monitor.rate_limiter._build_redis_client`) или
    ``None`` (тогда fail-open).
    """

    def __init__(self, redis_client, per_min: int, per_day: int) -> None:
        self.client = redis_client
        self.per_min = int(per_min)
        self.per_day = int(per_day)
        self._script = None
        if redis_client is not None:
            try:
                self._script = redis_client.register_script(_LUA_INCR)
            except Exception as e:  # pragma: no cover - defensive
                logger.warning("GatewayQuota: register_script failed: %s", e)
                self._script = None

    def check_and_consume(self, key_name: str) -> "tuple[bool, int]":
        """Учесть один запрос ключа ``key_name``.

        Returns:
            ``(allowed, retry_after_sec)`` — ``allowed=False`` при превышении
            минутной или суточной квоты; ``retry_after_sec`` = сколько ждать
            до конца исчерпанного окна. Redis недоступен → ``(True, 0)``.
        """
        if self._script is None:
            return True, 0
        now = int(time.time())
        try:
            min_window = now // 60
            day_window = now // 86400
            min_count = int(
                self._script(keys=[f"{REDIS_KEY_PREFIX}:min:{key_name}:{min_window}"], args=[60])
            )
            day_count = int(
                self._script(keys=[f"{REDIS_KEY_PREFIX}:day:{key_name}:{day_window}"], args=[86400])
            )
        except Exception as e:
            # Redis отвалился в момент запроса — не блокируем (fail-open).
            logger.warning("GatewayQuota: Redis call failed for %s — fail-open: %s", key_name, e)
            return True, 0

        if min_count > self.per_min:
            return False, 60 - (now % 60)
        if day_count > self.per_day:
            return False, 86400 - (now % 86400)
        return True, 0
