"""Liveness демона ВК-бота (``setka-vk-bot``): heartbeat в Redis + сторож (Этап 5).

Демон пишет unix-ts после **каждого реального HTTP-ответа Long Poll** —
с событиями, пустого (LP_WAIT истёк) или ``failed`` 1/2/3: «свежий» значит
«демон действительно опрашивает ВК». Сетевой ``{}`` и выключенный конфигом бот
ключ не трогают — сломанный токен или getLongPollServer видны как тишина.

Сторож (beat, каждые 10 минут) читает ключ: протух дольше порога — Telegram-алёрт
с cooldown 6 ч. Ключа нет — ``unknown:no-heartbeat`` без алёрта, как у сводок и
классификатора (свежий деплой ≠ поломка; ключ появляется через ~30 с после
первого старта и живёт 14 суток). Бот выключен конфигом — ``skipped:bot-off``
(retired ≠ dead, как ``no-sources`` у Радара).

Ключ выделенный (не под ``setka:digest_last_published:*`` — иначе
``all_heartbeats()`` показал бы его на дашборде как тему сводок).
"""

from __future__ import annotations

import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

HEARTBEAT_KEY = "setka:vkbot:heartbeat"
_HEARTBEAT_TTL = 14 * 24 * 3600
COOLDOWN_KEY = "setka:vkbot:stale_cooldown"
ALERT_COOLDOWN_SECONDS = 6 * 3600
#: Порог: плановый рестарт (~10 с) и деплой не должны давать алёрт.
DEFAULT_MAX_AGE_SECONDS = 15 * 60
#: Лежащий Redis не должен давать строку WARNING на каждый тик Long Poll (25 с).
_WARN_EVERY = 300.0
_last_warn_at = 0.0


def _redis():
    from modules.bulletin_heartbeat import _redis as _r

    return _r()


def _warn(msg: str) -> None:
    global _last_warn_at
    now = time.monotonic()
    if now - _last_warn_at >= _WARN_EVERY:
        _last_warn_at = now
        logger.warning(msg, exc_info=True)


def touch(client=None, *, ts: Optional[float] = None) -> bool:
    """Отметить живой опрос Long Poll (best-effort, никогда не бросает)."""
    try:
        client = client if client is not None else _redis()
        if client is None:
            _warn("vk_bot heartbeat skipped: redis unavailable")
            return False
        client.setex(HEARTBEAT_KEY, _HEARTBEAT_TTL, str(int(ts if ts is not None else time.time())))
        return True
    except Exception:  # noqa: BLE001 - наблюдаемость не роняет демон
        _warn("vk_bot heartbeat write failed")
        return False


def last_ts(client=None) -> Optional[int]:
    """Unix-ts последнего опроса или ``None`` (нет ключа / нет Redis)."""
    try:
        client = client if client is not None else _redis()
        if client is None:
            return None
        val = client.get(HEARTBEAT_KEY)
        return int(val) if val else None
    except Exception:  # noqa: BLE001
        logger.debug("vk_bot heartbeat read failed", exc_info=True)
        return None


def build_alert_text(age_seconds: Optional[float], max_age_seconds: int) -> str:
    """Текст Telegram-алёрта (HTML). Чистая."""
    last = f"{age_seconds / 60:.0f} мин назад" if age_seconds is not None else "никогда"
    return (
        "⚠️ <b>SETKA: ВК-бот САРАФАНа молчит</b>\n\n"
        f"Демон не опрашивал Long Poll дольше порога ({max_age_seconds // 60} мин): "
        f"последний опрос — <b>{last}</b>. Клиенты пишут в личку сообщества и не "
        "получают ответа.\n\n"
        "Проверь: <code>systemctl status setka-vk-bot</code>, "
        "<code>tail -n 50 ~/SETKA/logs/vk-bot.log</code>."
    )


async def maybe_alert_stale_vk_bot(
    *,
    telegram_token: Optional[str],
    chat_id: Optional[str],
    now: Optional[float] = None,
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
    community=None,
) -> str:
    """Сторож: протухший heartbeat → Telegram-алёрт с cooldown.

    Статусы по порядку проверок: ``skipped:bot-off`` | ``skipped:no-redis`` |
    ``unknown:no-heartbeat`` | ``fresh`` | ``skipped:no-telegram-config`` |
    ``skipped:cooldown`` | ``alert-sent`` | ``error:http-N`` | ``error:…``.
    """
    if community is None:
        from modules.ad_cabinet.vk_bot.notify import community as _community

        community = _community
    if await community() is None:
        return "skipped:bot-off"
    client = _redis()
    if client is None:
        return "skipped:no-redis"
    ts = last_ts(client)
    if ts is None:
        logger.warning("vk_bot heartbeat: no key yet (fresh deploy or daemon never polled)")
        return "unknown:no-heartbeat"
    current = now if now is not None else time.time()
    age = current - ts
    if age < max_age_seconds:
        return "fresh"
    if not telegram_token or not chat_id:
        return "skipped:no-telegram-config"
    try:
        if client.get(COOLDOWN_KEY):
            return "skipped:cooldown"
    except Exception:  # noqa: BLE001
        pass
    try:
        from modules import telegram_http as tg_http

        resp = tg_http.post(
            f"https://api.telegram.org/bot{telegram_token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": build_alert_text(age, max_age_seconds),
                "parse_mode": "HTML",
            },
        )
        if resp.status_code != 200:
            logger.warning("vk_bot stale alert failed: %s", resp.status_code)
            return "error:http-" + str(resp.status_code)
        client.setex(COOLDOWN_KEY, ALERT_COOLDOWN_SECONDS, "1")
        logger.info("vk_bot stale alert sent (age %.0f s)", age)
        return "alert-sent"
    except Exception as exc:  # noqa: BLE001 - сеть
        logger.error("vk_bot stale alert failed: %s", exc)
        return "error:" + str(exc)


__all__ = [
    "HEARTBEAT_KEY",
    "COOLDOWN_KEY",
    "DEFAULT_MAX_AGE_SECONDS",
    "touch",
    "last_ts",
    "build_alert_text",
    "maybe_alert_stale_vk_bot",
]
