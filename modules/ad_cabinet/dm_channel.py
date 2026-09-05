"""Пауза DM-канала сообщества после VK 9/14 (Этап 3, решение владельца 2026-09-05).

Коды 9 (flood control) и 14 (captcha) говорят о **потоке**, а не о токене:
токен жив, но VK просит замолчать. Раньше такой ответ уходил в общую ветку
ошибок, и следующий пинг/приветствие/оффер снова стучал в VK тем же каналом.
Здесь пауза ставится на **канал** — сообщество, от имени которого шлём ЛС
(``community_id``), — на срок из :func:`modules.promotion.vk_errors.classify_promo_error`
(9 → сутки, 14 → 6 часов). Токен не выключается.

Хранилище — Redis (ключ на сообщество, TTL = срок паузы) с in-process
запасом на случай лежащего Redis, как в :mod:`modules.ad_cabinet.owner_ping`.
Всё сетевое инъектируемо; чистая логика покрыта тестами без Redis.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Dict, Optional

logger = logging.getLogger(__name__)

KEY = "setka:dm_pause:{cid}"
PAUSE_CODES = (9, 14)
_ALERT_TTL = 6 * 3600

_lock = threading.Lock()
_local: Dict[int, float] = {}  # cid -> unix-время конца паузы (запас без Redis)


def _redis():
    """Тот же клиент и та же защита от лежащего Redis, что у пингов владельцу."""
    from modules.ad_cabinet import owner_ping

    try:
        return owner_ping._redis()
    except Exception:  # noqa: BLE001
        owner_ping._mark_redis_down()
        return None


def pause_seconds(code: Optional[int]) -> int:
    """Срок паузы для кода VK; 0 — код не про поток."""
    if code not in PAUSE_CODES:
        return 0
    from modules.promotion.vk_errors import classify_promo_error

    return int(classify_promo_error(int(code)).module_cooldown_seconds or 0)


def paused_until(community_id: int, *, now: Optional[float] = None) -> Optional[datetime]:
    """До какого момента (UTC naive) канал молчит; ``None`` — канал открыт."""
    cid = abs(int(community_id))
    now = time.time() if now is None else now
    client = _redis()
    if client is not None:
        try:
            raw = client.get(KEY.format(cid=cid))
            if raw:
                ts = float(raw)
                if ts > now:
                    return datetime.utcfromtimestamp(ts)
                return None
        except Exception:  # noqa: BLE001 - Redis лёг → локальный слой
            from modules.ad_cabinet import owner_ping

            owner_ping._mark_redis_down()
    with _lock:
        ts = _local.get(cid)
        if ts and ts > now:
            return datetime.utcfromtimestamp(ts)
        _local.pop(cid, None)
    return None


def pause(community_id: int, seconds: int, *, now: Optional[float] = None) -> datetime:
    """Поставить канал на паузу на ``seconds`` (продлевает, если уже на паузе)."""
    cid = abs(int(community_id))
    now = time.time() if now is None else now
    until = now + max(1, int(seconds))
    client = _redis()
    if client is not None:
        try:
            client.set(KEY.format(cid=cid), str(until), ex=max(1, int(seconds)))
        except Exception:  # noqa: BLE001
            from modules.ad_cabinet import owner_ping

            owner_ping._mark_redis_down()
    with _lock:
        _local[cid] = max(until, _local.get(cid, 0.0))
    return datetime.utcfromtimestamp(until)


def note_error(
    community_id: int,
    code: Optional[int],
    *,
    now: Optional[float] = None,
    alert=None,
) -> Optional[datetime]:
    """Учесть ошибку отправки: 9/14 → пауза канала + алёрт владельцу (дедуп 6 ч).

    Возвращает конец паузы или ``None``, если код не про поток.
    """
    seconds = pause_seconds(code)
    if not seconds:
        return None
    until = pause(community_id, seconds, now=now)
    hours = seconds // 3600
    text = (
        f"⏸ VK {code} для ЛС сообщества {abs(int(community_id))}: канал на паузе "
        f"{hours} ч (до {until:%d.%m %H:%M} UTC). Токен не выключен — это про поток."
    )
    try:
        if alert is not None:
            alert(text)
        else:
            from modules.ad_cabinet import owner_ping

            owner_ping.notify_owner(
                text, dedup_key=f"dm_pause:{abs(int(community_id))}", dedup_ttl=_ALERT_TTL
            )
    except Exception:  # noqa: BLE001 - алёрт не роняет отправку
        logger.warning("dm_channel alert failed", exc_info=True)
    logger.warning("dm_channel: community %s paused until %s (VK %s)", community_id, until, code)
    return until


def reset_for_tests() -> None:
    with _lock:
        _local.clear()


__all__ = ["paused_until", "pause", "note_error", "pause_seconds", "PAUSE_CODES", "KEY"]
