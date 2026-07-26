"""Учёт расхода VK-запросов по токенам (заказ владельца 2026-07-25).

Зачем. До этого модуля «сколько запросов сжёг каждый токен» нигде не считалось:
в ``vk_tokens`` был только ``last_used`` (штамп последнего выбора), а READ-токен
выбирается **один на волну парсинга** — по штампу нельзя понять, кто вынес
основную нагрузку, а кто простоял. Из-за этого нельзя было ни раскидать чтение
равномерно, ни заметить, что один токен подошёл к суточному лимиту VK
(error 29) раньше остальных.

Что считаем. Каждый фактический вызов VK API — на единственном узком месте
:meth:`modules.vk_monitor.vk_client.VKClient._enforce_rate_limit`, через которое
проходит любой запрос клиента. Счётчики посуточные, с разбивкой по методу.

Где храним. Redis (тот же инстанс, что у rate-limiter'а), ключ
``setka:vk_usage:<YYYY-MM-DD>:<TOKEN_NAME>`` — HASH с полями ``total`` и
``m:<method>``. TTL 8 суток: недельное окно для отчёта плюс сутки запаса.
Redis, а не Postgres, потому что это счётчик в горячем пути парсинга (сотни
инкрементов на волну) — писать в БД на каждый вызов недопустимо. Потеря
счётчиков при флаше Redis не ломает ничего: они управляют балансировкой и
отчётом, а не корректностью.

Имя токена. ``VKClient`` знает только строку токена. Сопоставление
«строка → имя» ведёт process-local реестр, который наполняет
:class:`modules.vk_token_router.TokenPolicy` в момент выдачи кандидата. Токен
в Redis-ключ **не попадает** — только имя; если имя неизвестно (клиент собран
в обход роутера), ключом станет ``UNKNOWN:<sha256[:8]>``, что видно в отчёте
как «расход мимо роутера» — тоже полезный сигнал.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from datetime import date, timedelta
from typing import Dict, Optional

logger = logging.getLogger(__name__)

REDIS_KEY_PREFIX = "setka:vk_usage"
USAGE_TTL_SECONDS = 8 * 24 * 3600

# Реестр «строка токена → имя». Заполняется роутером при выдаче кандидата.
_name_by_token: Dict[str, str] = {}
_registry_lock = threading.Lock()

# In-memory fallback, когда Redis недоступен: {day: {name: {field: count}}}.
_memory_usage: Dict[str, Dict[str, Dict[str, int]]] = {}
_memory_lock = threading.Lock()

_redis_client = None
_redis_probed = False
_redis_lock = threading.Lock()


def _token_fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:8]


def register_token_name(token: str, name: str) -> None:
    """Запомнить, какому имени соответствует строка токена.

    Идемпотентна. Вызывается роутером на каждой выдаче кандидата — цена
    пренебрежимая, зато реестр самозаполняется во всех процессах (Celery
    prefork, web), каждый в своей памяти.
    """
    if not token or not name:
        return
    with _registry_lock:
        _name_by_token[token] = name.upper()


def resolve_token_name(token: str) -> str:
    """Имя токена по его строке; ``UNKNOWN:<fp>`` если роутер его не выдавал."""
    if not token:
        return "UNKNOWN:empty"
    with _registry_lock:
        name = _name_by_token.get(token)
    return name or f"UNKNOWN:{_token_fingerprint(token)}"


def _get_redis():
    """Ленивый sync-клиент Redis; ``None`` если недоступен (тогда — память)."""
    global _redis_client, _redis_probed
    if _redis_probed:
        return _redis_client
    with _redis_lock:
        if _redis_probed:
            return _redis_client
        try:
            import redis as redis_sync

            from config.runtime import REDIS

            client = redis_sync.Redis(
                host=REDIS["host"],
                port=int(REDIS["port"]),
                db=int(REDIS.get("db", 0)),
                socket_timeout=2.0,
                socket_connect_timeout=2.0,
                decode_responses=True,
            )
            client.ping()
            _redis_client = client
        except Exception as e:
            logger.warning("token_usage: Redis недоступен, счёт идёт в память: %s", e)
            _redis_client = None
        _redis_probed = True
    return _redis_client


def _today_moscow() -> date:
    """Текущие сутки по Москве — граница обнуления счётчиков.

    Счётчики посуточные, то есть балансировка чтения смотрит на расход
    «с полуночи». Полночь должна быть **московской**, как и всё расписание
    проекта: прод-сервер сейчас в MSK, и ``date.today()`` давал верный ответ
    случайно — переезд машины в UTC молча сдвинул бы обнуление на 03:00 MSK и
    перекосил бы балансировку на три часа каждую ночь. Привязываемся явно.
    """
    from utils.timezone import now_moscow

    return now_moscow().date()


def _day_key(day: Optional[date] = None) -> str:
    return (day or _today_moscow()).isoformat()


def _record_in_memory(day: str, name: str, method: str) -> None:
    with _memory_lock:
        bucket = _memory_usage.setdefault(day, {}).setdefault(name, {})
        bucket["total"] = bucket.get("total", 0) + 1
        if method:
            field = f"m:{method}"
            bucket[field] = bucket.get(field, 0) + 1
        # Память не должна расти вечно — держим только последние 8 суток.
        if len(_memory_usage) > 8:
            for stale in sorted(_memory_usage)[:-8]:
                _memory_usage.pop(stale, None)


def record_call(token: str, method: str = "") -> None:
    """Учесть один вызов VK API. Best-effort: не бросает наружу никогда.

    Вызывается из горячего пути парсинга, поэтому любые сбои учёта
    проглатываются — статистика не имеет права ронять сбор.
    """
    try:
        name = resolve_token_name(token)
        day = _day_key()
        client = _get_redis()
        if client is None:
            _record_in_memory(day, name, method)
            return
        key = f"{REDIS_KEY_PREFIX}:{day}:{name}"
        pipe = client.pipeline()
        pipe.hincrby(key, "total", 1)
        if method:
            pipe.hincrby(key, f"m:{method}", 1)
        pipe.expire(key, USAGE_TTL_SECONDS)
        pipe.execute()
    except Exception as e:  # pragma: no cover — учёт не должен ломать сбор
        logger.debug("token_usage.record_call failed: %s", e)


def get_usage(day: Optional[date] = None) -> Dict[str, Dict[str, int]]:
    """Расход за сутки: ``{имя_токена: {"total": N, "m:<method>": N}}``."""
    day_str = _day_key(day)
    client = _get_redis()
    if client is None:
        with _memory_lock:
            return {n: dict(v) for n, v in _memory_usage.get(day_str, {}).items()}
    out: Dict[str, Dict[str, int]] = {}
    try:
        prefix = f"{REDIS_KEY_PREFIX}:{day_str}:"
        for key in client.scan_iter(match=f"{prefix}*", count=200):
            name = key[len(prefix) :]
            raw = client.hgetall(key)
            out[name] = {k: int(v) for k, v in raw.items()}
    except Exception as e:  # pragma: no cover — отчёт не критичен
        logger.warning("token_usage.get_usage failed: %s", e)
    return out


def get_usage_window(days: int = 7) -> Dict[str, Dict[str, Dict[str, int]]]:
    """Расход за последние ``days`` суток: ``{дата: {имя: {поле: N}}}``."""
    today = _today_moscow()
    return {
        _day_key(today - timedelta(days=offset)): get_usage(today - timedelta(days=offset))
        for offset in range(max(1, int(days)))
    }


def get_calls_today() -> Dict[str, int]:
    """``{имя_токена: сколько запросов сегодня}`` — вход для балансировки READ."""
    return {name: int(fields.get("total", 0)) for name, fields in get_usage().items()}


def reset_for_tests() -> None:
    """Сбросить реестр, кеш Redis-клиента и память (используется в фикстурах)."""
    global _redis_client, _redis_probed
    with _registry_lock:
        _name_by_token.clear()
    with _memory_lock:
        _memory_usage.clear()
    with _redis_lock:
        _redis_client = None
        _redis_probed = False
