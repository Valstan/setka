"""Конфиг VK-шлюза (ворота доступа в VK для других проектов @valstan).

Шлюз даёт другим проектам read-only доступ к VK через HTTP: проект шлёт
задачу → SARAFAN исполняет её своим токеном (со своего IP, под своим
rate-limit/cooldown) → возвращает JSON. Токен наружу не уходит.

Секреты — только в env (``/etc/setka/setka.env``), как и VK-токены. Здесь —
тонкая обёртка над ``config.runtime`` (парсеры не дублируем) + статический
allowlist read-методов.

Env vars:
  GATEWAY_KEY_<PROJECT>     # секрет на проект (как VK_TOKEN_<NAME>),
                            # напр. GATEWAY_KEY_BRAIN=...
  GATEWAY_QUOTA_PER_MIN=30  # минутная квота запросов на ключ
  GATEWAY_QUOTA_PER_DAY=5000 # суточная квота на ключ
  GATEWAY_DISABLED=0        # аварийный kill-switch (1/true/yes/on → выкл.)
  GATEWAY_REQUESTS_RETENTION_DAYS=90  # сколько дней хранить лог gateway_requests
"""

from __future__ import annotations

import os
from typing import Dict

from config.runtime import _collect_prefixed_tokens

# Allowlist read-методов VK API. Только чтение — никаких wall.post/edit/delete,
# messages.send, likes.add, wall.repost. Расширять осознанно: каждый метод
# должен быть read-only и безопасным для исполнения нашим токеном.
GATEWAY_READ_METHODS: frozenset = frozenset(
    {
        "groups.getById",
        "groups.getMembers",
        "groups.search",
        "groups.isMember",
        "wall.get",
        "wall.getById",
        "wall.getComments",
        "wall.getReposts",
        "users.get",
        "users.getFollowers",
        "users.getSubscriptions",
        "likes.getList",
        "board.getTopics",
        "board.getComments",
        "photos.get",
        "photos.getAlbums",
        "video.get",
        "utils.resolveScreenName",
        "database.getCities",
        "database.getCountries",
        "newsfeed.search",
        "stats.getPostReach",
    }
)


def get_gateway_keys() -> Dict[str, str]:
    """Вернуть ``{PROJECT_NAME_UPPER: secret}`` из ``GATEWAY_KEY_<PROJECT>``.

    Тот же механизм, что у ``VK_TOKEN_<NAME>`` — читается из env при каждом
    вызове (тесты и ротация ключей не требуют рестарта парсеров модуля).
    """
    return _collect_prefixed_tokens("GATEWAY_KEY_")


def get_gateway_quota_per_min() -> int:
    """Минутная квота запросов на один API-ключ (env ``GATEWAY_QUOTA_PER_MIN``)."""
    try:
        return int(os.getenv("GATEWAY_QUOTA_PER_MIN", "30"))
    except ValueError:
        return 30


def get_gateway_quota_per_day() -> int:
    """Суточная квота запросов на один API-ключ (env ``GATEWAY_QUOTA_PER_DAY``)."""
    try:
        return int(os.getenv("GATEWAY_QUOTA_PER_DAY", "5000"))
    except ValueError:
        return 5000


def get_gateway_global_quota_per_min() -> int:
    """Агрегатный минутный бюджет шлюза — сумма по ВСЕМ потребителям.

    Env ``GATEWAY_GLOBAL_QUOTA_PER_MIN`` (дефолт 120). Анти-бан-энфорс
    (мандат brain 2026-07-12): per-consumer квоты по отдельности могут
    суммарно продавить VK-лимит READ-токенов; этот слой держит общую
    нагрузку шлюза ниже лимитов VK с запасом. Превышение → 429 потребителю
    до расхода токена. 0 или отрицательное → слой выключен.
    """
    try:
        return int(os.getenv("GATEWAY_GLOBAL_QUOTA_PER_MIN", "120"))
    except ValueError:
        return 120


def gateway_disabled() -> bool:
    """Kill-switch шлюза (env ``GATEWAY_DISABLED``). Дефолт — включён (False)."""
    return os.getenv("GATEWAY_DISABLED", "0").strip().lower() in ("1", "true", "yes", "on")


def get_gateway_requests_retention_days() -> int:
    """Сколько дней хранить строки ``gateway_requests`` (env, дефолт 90).

    Лог запросов к шлюзу растёт постоянно (включая 401/429); суточная beat-таска
    ``prune_gateway_requests`` чистит строки старше этого порога.
    """
    try:
        return int(os.getenv("GATEWAY_REQUESTS_RETENTION_DAYS", "90"))
    except ValueError:
        return 90
