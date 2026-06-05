"""Redis-heartbeat «последняя успешная публикация дайджеста» + watchdog-алёрт.

**Зачем не Prometheus:** gauge ``setka_digest_last_published_timestamp`` на
проде ненадёжен (multiproc-mmap пуст несмотря на реальные публикации — давняя
боль вокруг PR #75). Для алёрта «давно нет дайджестов» нужен простой надёжный
сигнал: пишем unix-ts в Redis из единой точки
``monitoring.metrics.track_digest_published`` (она вызывается на ВСЕХ путях
публикации — theme-волны и каскад), а beat-watchdog читает и при протухании
шлёт Telegram-алёрт.

Ключ ``setka:digest_last_published:<topic>`` (Redis db=1, как у
``NotificationsStorage`` — переиспользуем его клиент, чтобы не плодить
коннект-параметры).
"""

from __future__ import annotations

import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

KEY_PREFIX = "setka:digest_last_published"

# Heartbeat живёт заметно дольше любого мыслимого порога простоя.
_HEARTBEAT_TTL_SECONDS = 14 * 24 * 3600

# Cooldown алёрта — не спамить, пока простой длится.
ALERT_COOLDOWN_SECONDS = 6 * 3600

# Порог простоя по умолчанию (часы). novost-волны идут 6×/сутки
# (6:40/11:40/12:40/16:40/18:40/20:40 MSK), макс. дневной зазор ~5ч
# (6:40→11:40) → 6ч с запасом не даёт ложных срабатываний.
DEFAULT_MAX_AGE_HOURS = 6

_redis_client = None


def _redis():
    """Лениво-кэшированный Redis-клиент (db=1, decode_responses=True).

    Переиспользует параметры подключения ``NotificationsStorage`` — единый
    источник настроек Redis для app-level ключей.
    """
    global _redis_client
    if _redis_client is None:
        try:
            from modules.notifications.storage import NotificationsStorage

            _redis_client = NotificationsStorage().redis_client
        except Exception:  # pragma: no cover - инфраструктурный сбой
            logger.debug("redis init failed for digest heartbeat", exc_info=True)
            return None
    return _redis_client


def mark_published(topic: str, *, ts: Optional[float] = None) -> None:
    """Отметить успешную публикацию дайджеста темы (best-effort, не падает).

    Вызывается из ``track_digest_published`` при ``result == "success"``.
    """
    if not topic:
        return
    try:
        client = _redis()
        if client is None:
            return
        client.setex(
            f"{KEY_PREFIX}:{topic}",
            _HEARTBEAT_TTL_SECONDS,
            str(int(ts if ts is not None else time.time())),
        )
    except Exception:  # pragma: no cover - наблюдаемость не должна ломать публикацию
        # WARNING (не debug): на проде LOG_LEVEL=INFO глушил debug, из-за чего
        # «heartbeat не пишется» оставалось незамеченным (инцидент 2026-06-05).
        logger.warning("digest heartbeat write failed (%s)", topic, exc_info=True)


def last_published_ts(topic: str) -> Optional[int]:
    """Unix-ts последней успешной публикации темы, либо ``None``."""
    try:
        client = _redis()
        if client is None:
            return None
        val = client.get(f"{KEY_PREFIX}:{topic}")
        return int(val) if val else None
    except Exception:
        logger.debug("digest heartbeat read failed (%s)", topic, exc_info=True)
        return None


def all_heartbeats() -> dict[str, int]:
    """Все ``topic → unix-ts`` из Redis (best-effort, не падает).

    Сканирует ключи ``setka:digest_last_published:*``, исключая служебные
    cooldown-ключи (``…:stale_alert_cooldown:<topic>``). Возвращает ``{}`` при
    любом инфраструктурном сбое — наблюдаемость не должна валить вызывающего.
    Используется дашбордом (``/api/monitoring/heartbeat``) для показа свежести
    публикаций по всем темам разом.
    """
    out: dict[str, int] = {}
    try:
        client = _redis()
        if client is None:
            return out
        prefix = f"{KEY_PREFIX}:"
        scan_iter = getattr(client, "scan_iter", None)
        keys = scan_iter(match=f"{prefix}*") if callable(scan_iter) else client.keys(f"{prefix}*")
        for raw in keys:
            key = raw.decode() if isinstance(raw, (bytes, bytearray)) else raw
            topic = key[len(prefix) :]
            # cooldown-ключи начинаются с "stale_alert_cooldown:" — не темы
            if not topic or topic.startswith("stale_alert_cooldown"):
                continue
            val = client.get(key)
            if val is None:
                continue
            try:
                out[topic] = int(val)
            except (TypeError, ValueError):
                continue
    except Exception:  # pragma: no cover - инфраструктурный сбой
        logger.debug("digest heartbeat scan failed", exc_info=True)
    return out


def maybe_alert_stale_digest(
    *,
    topic: str = "novost",
    max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
    telegram_token: Optional[str] = None,
    chat_id: Optional[str] = None,
    dashboard_url: Optional[str] = None,
    now: Optional[float] = None,
) -> str:
    """Если heartbeat темы старше порога — Telegram-алёрт (с cooldown).

    Возвращает статус-строку: ``fresh`` | ``unknown:no-heartbeat`` |
    ``skipped:no-telegram-config`` | ``skipped:cooldown`` | ``alert-sent`` |
    ``error:…``.

    **None heartbeat НЕ алёртит**: нельзя отличить «свежий деплой, ещё не было
    волны» от «сломано навсегда». Алёртим только на ПРОТУХШИЙ существующий
    heartbeat (был сигнал → пропал = что-то сломалось в beat/worker). novost
    пишется ≥6×/сутки, так что heartbeat появляется в первые часы после деплоя.
    """
    current = now if now is not None else time.time()
    ts = last_published_ts(topic)
    if ts is None:
        return "unknown:no-heartbeat"

    age = current - ts
    if age < max_age_hours * 3600:
        return "fresh"

    if not telegram_token or not chat_id:
        return "skipped:no-telegram-config"

    client = _redis()
    cooldown_key = f"{KEY_PREFIX}:stale_alert_cooldown:{topic}"
    try:
        if client is not None and client.get(cooldown_key):
            return "skipped:cooldown"
    except Exception:
        pass

    hours = age / 3600.0
    parts = [
        "⚠️ <b>SETKA: давно нет дайджестов</b>\n",
        f"Тема <b>{topic}</b> не публиковалась <b>{hours:.1f} ч</b> " f"(порог {max_age_hours} ч).",
        "\nВероятно, упал beat/worker или все публикации падают. Проверь: "
        "<code>systemctl status setka-celery-beat setka-celery-worker</code>.",
    ]
    if dashboard_url:
        parts.append(f"\n🔗 <a href='{dashboard_url}'>Открыть SETKA</a>")
    message = "\n".join(parts)

    try:
        import requests

        resp = requests.post(
            f"https://api.telegram.org/bot{telegram_token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
        if resp.status_code != 200:
            logger.warning("stale-digest alert failed: %s %s", resp.status_code, resp.text[:200])
            return "error:http-" + str(resp.status_code)
        if client is not None:
            client.setex(cooldown_key, ALERT_COOLDOWN_SECONDS, "1")
        logger.info("Sent stale-digest alert for topic=%s age=%.1fh", topic, hours)
        return "alert-sent"
    except Exception as exc:
        logger.error("Failed to send stale-digest alert: %s", exc)
        return "error:" + str(exc)
