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

Хранение (мандат brain 2026-08-01, 🔴): **в БД лежит только SHA-256 секрета**,
плюс короткий префикс для опознания человеком. Компрометация дампа больше не
отдаёт ключи потребителей. Предъявленный ключ хэшируется и ищется по индексу —
это заодно убрало перебор всех строк на каждом запросе.

**Почему sha256, а не scrypt, которым хэшируются пароли** (``modules/radar/auth.py``).
Пароль выбирает человек: он короткий и угадываемый, поэтому хэш обязан быть
намеренно медленным. Ключ шлюза — ``secrets.token_urlsafe(32)``, 256 бит
машинной энтропии: перебирать нечего, зато проверка идёт на **каждом** запросе
потребителя. Медленный KDF здесь не добавил бы стойкости, а стоил бы задержки
на всём трафике шлюза. Не «унифицируй» это со схемой паролей.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Optional

from sqlalchemy import select

from config.gateway import get_gateway_keys as get_env_gateway_keys

logger = logging.getLogger(__name__)

# Сколько первых символов секрета хранить открыто — чтобы человек мог опознать
# ключ в перечне и в разборе отказов, не имея его значения. У 256-битного
# ключа остаётся с запасом (энтропия уменьшается на длину префикса).
SECRET_PREFIX_LEN = 6


def hash_gateway_secret(secret: str) -> str:
    """SHA-256 секрета в hex — то, что лежит в БД (см. докстринг модуля)."""
    return hashlib.sha256(secret.encode()).hexdigest()


def gateway_secret_prefix(secret: str) -> str:
    """Первые символы секрета — опознавательный знак, не средство проверки."""
    return secret[:SECRET_PREFIX_LEN]


async def resolve_gateway_key_name(presented: str) -> Optional[str]:
    """Имя проекта по предъявленному ключу, либо ``None``.

    Порядок: активная строка БД по хэшу → env-bootstrap. Имя, у которого в БД
    есть **неактивная** строка, через env не воскресает (та же семантика, что
    была у merge-словаря). Недоступная БД → чистый env-набор: шлюз не должен
    лечь из-за БД, защита VK-токена держится своими слоями.
    """
    if not presented:
        return None

    presented_hash = hash_gateway_secret(presented)
    db_names: set = set()
    try:
        from database.connection import AsyncSessionLocal
        from database.models import GatewayKey

        async with AsyncSessionLocal() as session:
            rows = (await session.execute(select(GatewayKey))).scalars().all()
    except Exception as e:  # defensive (БД недоступна / таблицы ещё нет)
        logger.warning("gateway keys: DB read failed — falling back to env only: %s", e)
        return _match_env(presented, blocked=set())

    for row in rows:
        db_names.add(row.name)
        if not row.is_active or not row.secret_sha256:
            continue
        if hmac.compare_digest(row.secret_sha256, presented_hash):
            return row.name
    return _match_env(presented, blocked=db_names)


def _match_env(presented: str, blocked: set) -> Optional[str]:
    """Env-ключи: сравнение со значением (в env секрет и лежит значением).

    ``blocked`` — имена, у которых уже есть строка в БД: для них env не
    авторитетен ни в какую сторону.
    """
    for name, secret in get_env_gateway_keys().items():
        if name in blocked or not secret:
            continue
        if hmac.compare_digest(presented, secret):
            return name
    return None


# Перечень «ключ есть в env, но не в БД» строит сам scripts/list_gateway_keys.py
# (get_env_gateway_keys + сверка с GatewayKey, строки 119 и 207). Здешняя
# get_bootstrap_env_keys делала ровно то же и не вызывалась ни разу — снята
# 2026-09-04 прогоном /deadcode, чтобы одна задача не жила в двух местах.
