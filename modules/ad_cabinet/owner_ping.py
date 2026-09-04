"""Telegram-пинги владельцу и троттлы телеметрии кабинета (best-effort).

Одна точка отправки для событий кабинета (заказ на модерации, новый клиент,
чат, ошибка у клиента) + две примитива анти-шума:

* ``ping_dedup_pass``  — «не чаще раза в ttl на ключ» (SET NX EX);
* ``event_budget_pass`` — «не больше limit событий за ttl» (INCR+EXPIRE) —
  контент-независимый потолок: ротация текста ошибки не выбивает новые вёдра
  (блокер adversarial-ревью 2026-08-26).

Redis-клиент кэшируется на модуль (не connect-на-вызов), падение Redis
запоминается на 60 с (не жечь event loop реконнектами на каждый маячок), а
семантика троттлов при недоступном Redis держится **in-process fallback'ом**:
прод — один uvicorn-процесс, локальный словарь эквивалентен. Полный отказ
обоих слоёв читается как «пропусти» для пингов (молчание про ошибку клиента
дороже спама) — записи в таймлайн при этом всё равно ограничены fallback'ом.

Вызывать из async-хендлеров через ``asyncio.to_thread`` — здесь синхронные
сокеты (Redis, requests), event loop блокировать нельзя (блокер ревью).
Любая ошибка глотается: уведомление не роняет действие клиента.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Порядок перебора бот-токенов — как у alert-рассылок discovery.
_BOT_KEYS = ("VALSTANBOT", "ALERT", "AFONYA")
_SEND_TIMEOUT = 5  # секунд на Telegram; висящий TG не должен держать запрос 10с

_REDIS_DOWN_TTL = 60.0  # сколько не трогать Redis после падения

_lock = threading.Lock()
_redis_client = None
_redis_down_until = 0.0
_local_marks: Dict[str, float] = {}  # in-process fallback: ключ -> истечение
_local_counts: Dict[str, List[float]] = {}  # ключ -> [счётчик, истечение]


def stable_digest(text: str) -> str:
    """Короткий стабильный отпечаток текста для ключей троттла.

    Встроенный ``hash()`` соло-соление PYTHONHASHSEED меняет на каждом рестарте
    и в каждом воркере — ключи «плавали» бы между деплоями (блокер ревью).
    """
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:10]


def _redis():
    """Кэшированный клиент либо ``None`` (Redis недавно падал — не трогаем)."""
    global _redis_client, _redis_down_until
    if time.monotonic() < _redis_down_until:
        return None
    if _redis_client is None:
        from modules.vk_monitor.rate_limiter import _build_redis_client

        _redis_client = _build_redis_client()
    return _redis_client


def _mark_redis_down() -> None:
    global _redis_down_until, _redis_client
    _redis_down_until = time.monotonic() + _REDIS_DOWN_TTL
    _redis_client = None


def _local_prune(now: float) -> None:
    if len(_local_marks) > 2000:
        for k, exp in list(_local_marks.items()):
            if exp <= now:
                _local_marks.pop(k, None)
    if len(_local_counts) > 2000:
        for k, slot in list(_local_counts.items()):
            if slot[1] <= now:
                _local_counts.pop(k, None)


def ping_dedup_pass(key: str, *, ttl: int) -> bool:
    """True — ключ свободен (действуем); False — недавно срабатывали."""
    try:
        client = _redis()
        if client is not None:
            return bool(client.set(f"setka:cabinet_ping:{key}", "1", nx=True, ex=ttl))
    except Exception:  # noqa: BLE001 - Redis лёг → локальный слой
        _mark_redis_down()
    with _lock:
        now = time.monotonic()
        _local_prune(now)
        exp = _local_marks.get(key)
        if exp and exp > now:
            return False
        _local_marks[key] = now + ttl
        return True


def release_dedup(key: str) -> None:
    """Вернуть dedup-ключ (отправка не удалась — не молчать целый ttl)."""
    try:
        client = _redis()
        if client is not None:
            client.delete(f"setka:cabinet_ping:{key}")
    except Exception:  # noqa: BLE001
        _mark_redis_down()
    with _lock:
        _local_marks.pop(key, None)


def event_budget_pass(key: str, *, limit: int, ttl: int) -> bool:
    """True, пока событий по ключу в окне ``ttl`` меньше ``limit``.

    Контент-независимый потолок записи: сколько ни ротируй текст, больше
    ``limit`` строк в окно не запишется.
    """
    try:
        client = _redis()
        if client is not None:
            full = f"setka:cabinet_budget:{key}"
            n = int(client.incr(full))
            if n == 1:
                client.expire(full, ttl)
            return n <= limit
    except Exception:  # noqa: BLE001 - Redis лёг → локальный слой
        _mark_redis_down()
    with _lock:
        now = time.monotonic()
        _local_prune(now)
        slot = _local_counts.get(key)
        if not slot or slot[1] <= now:
            _local_counts[key] = [1, now + ttl]
            return True
        slot[0] += 1
        return slot[0] <= limit


def notify_owner(text: str, *, dedup_key: str | None = None, dedup_ttl: int = 3600) -> bool:
    """Отправить владельцу Telegram-сообщение. True — отправлено.

    ``dedup_key`` — не чаще раза в ``dedup_ttl`` на ключ; неудачная отправка
    возвращает ключ (иначе сбой TG глушил бы следующий честный пинг на час).
    Синхронна — из async-кода звать через ``asyncio.to_thread``.
    """
    try:
        if dedup_key and not ping_dedup_pass(dedup_key, ttl=dedup_ttl):
            return False
        sent = _send_telegram(text)
        if not sent and dedup_key:
            release_dedup(dedup_key)
        return sent
    except Exception:  # noqa: BLE001 - уведомление не роняет действие клиента
        logger.warning("owner ping failed", exc_info=True)
        if dedup_key:
            release_dedup(dedup_key)
        return False


def _send_telegram(text: str) -> bool:
    from config.runtime import TELEGRAM_ALERT_CHAT_ID, TELEGRAM_TOKENS
    from modules import telegram_http as tg_http

    bot_token: Optional[str] = None
    for key in _BOT_KEYS:
        bot_token = (TELEGRAM_TOKENS or {}).get(key)
        if bot_token:
            break
    if not bot_token:
        bot_token = next(iter((TELEGRAM_TOKENS or {}).values()), None)
    if not bot_token or not TELEGRAM_ALERT_CHAT_ID:
        logger.info("owner ping skipped: telegram not configured")
        return False
    resp = tg_http.post(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        json={
            "chat_id": TELEGRAM_ALERT_CHAT_ID,
            "text": text,
            "disable_web_page_preview": True,
        },
    )
    if not resp.ok:
        logger.warning("owner ping: telegram answered %s", resp.status_code)
    return bool(resp.ok)
