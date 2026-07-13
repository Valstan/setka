"""Ключи VK-шлюза: БД — единый источник, env — bootstrap-fallback.

Мандат brain 2026-07-12 + паттерн #072 (single source в БД): API-ключи
потребителей живут в таблице ``gateway_keys`` (миграция 059), выдаются через
``scripts/issue_gateway_key.py``. Env ``GATEWAY_KEY_<PROJECT>`` остаётся
двумя ролями:

* **bootstrap** — имя, которого в БД нет вовсе, работает из env (первый деплой,
  аварийный ключ при потерянной таблице);
* **аварийный fallback** — если БД недоступна целиком, шлюз живёт на env-ключах
  (fail-open в сторону env, как у токенов #336), а не падает.

Семантика merge — та же, что у ``TokenPolicy`` (#336): выключенный в БД ключ
(``is_active=false``) env НЕ воскрешает — строка в БД «главнее» env при
совпадении имени.
"""

from __future__ import annotations

import logging
from typing import Dict

from sqlalchemy import select

from config.gateway import get_gateway_keys as get_env_gateway_keys

logger = logging.getLogger(__name__)


async def get_effective_gateway_keys() -> Dict[str, str]:
    """Вернуть ``{PROJECT_NAME_UPPER: secret}`` — БД поверх env.

    Правила:
    * активная строка БД → ключ в выдаче (значение из БД, даже если env
      содержит другое);
    * НЕактивная строка БД → имя блокируется целиком (env не воскрешает);
    * env-имя без строки в БД → добавляется как bootstrap.

    Любая ошибка чтения БД → warning + чистый env-набор (аварийный fallback:
    шлюз не должен лечь из-за недоступной БД — защита токена остаётся на
    своих слоях).
    """
    env_keys = get_env_gateway_keys()
    try:
        from database.connection import AsyncSessionLocal
        from database.models import GatewayKey

        async with AsyncSessionLocal() as session:
            rows = (await session.execute(select(GatewayKey))).scalars().all()
    except Exception as e:  # defensive (БД недоступна / таблицы ещё нет)
        logger.warning("gateway keys: DB read failed — falling back to env only: %s", e)
        return dict(env_keys)

    effective: Dict[str, str] = {}
    db_names = set()
    for row in rows:
        db_names.add(row.name)
        if row.is_active and row.secret:
            effective[row.name] = row.secret
    for name, secret in env_keys.items():
        if name not in db_names:
            effective[name] = secret
    return effective
