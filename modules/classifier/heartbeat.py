"""Сторож «ИИ-фильтр молчит» — алёрт по факту отсутствия вердиктов.

**Зачем отдельный сторож, если таска и так логирует.** Инцидент 2026-08-19:
``DEEPSEEK_API_KEY`` не доехал до celery-воркера (vault был недоступен ровно в
секунду старта процесса, а ``bootstrap_secrets`` ходит туда один раз). Трое
суток таска ``classify_pending_posts`` исправно просыпалась каждые 3 часа,
забирала 200 постов, получала ``no_api_key`` на каждом чанке — и возвращала
``status: ok``. Наружу это выглядело здоровьем: сервисы ``active``, health 200,
beat шлёт задачи, worker их принимает. Отказ обнаружил человек, заметив, что
лента пустая.

**Сигнал берётся из продукта, а не из сердцебиения, которое пишет сам движок.**
Ключ ``last_verdict_at`` — максимум ``content_classifications.created_at``, то
есть след реально выполненной работы. Heartbeat-ключ, который таска ставит на
своём успешном завершении, в этом инциденте был бы свежим: таска ведь
завершалась успешно. Это ровно пул #145 — эталон нельзя брать у охраняемого,
и #133 — успех канала не доказывает, что по каналу шло содержимое.

**Порог.** Cron ИИ-фильтра — каждые 3 часа. Восемь часов = два пропущенных
прогона плюс запас на длинный прогон и перезапуск воркера; ложных срабатываний
на здоровой системе не даёт.

**Пустой ``last_verdict_at`` не алёртит** — по той же причине, что и в
``modules/bulletin_heartbeat``: «свежая база, вердиктов ещё не было» неотличимо
от «сломано навсегда». Алёртим только на протухший существующий след.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger(__name__)

COOLDOWN_KEY = "setka:classifier:stale_alert_cooldown"

# Не спамить, пока простой длится. Шесть часов = два тика cron'а ИИ-фильтра:
# напоминание приходит, пока проблема жива, но не чаще, чем есть новости.
ALERT_COOLDOWN_SECONDS = 6 * 3600

# Порог простоя в часах (cron — каждые 3 часа, см. docstring модуля).
DEFAULT_MAX_AGE_HOURS = 8.0


def _redis():
    """Redis для cooldown'а — тот же клиент, что у сторожа сводок (db=1)."""
    try:
        from modules.bulletin_heartbeat import _redis as _shared_redis

        return _shared_redis()
    except Exception as exc:  # noqa: BLE001 — сторож не должен падать
        logger.warning("classifier heartbeat: redis недоступен: %s", exc)
        return None


def verdict_age_hours(
    last_verdict_at: Optional[Any], *, now: Optional[datetime] = None
) -> Optional[float]:
    """Возраст последнего вердикта в часах. ``None`` = вердиктов не было вовсе.

    Принимает и ``datetime``, и ISO-строку: ``health_stats`` отдаёт метку уже
    сериализованной, и разбирать её в вызывающем — лишний повод ошибиться.

    ``created_at`` в БД — наивный UTC (колонка ``timestamp without time zone``),
    поэтому сравниваем с ``utcnow``, а не с локальным временем: на проде
    ``now()`` отдаёт MSK, и наивное сравнение промахнулось бы на три часа —
    ровно та граблина, что записана в handoff'е.
    """
    if last_verdict_at is None:
        return None
    if isinstance(last_verdict_at, str):
        try:
            last_verdict_at = datetime.fromisoformat(last_verdict_at.replace("Z", ""))
        except ValueError:
            return None
    if not isinstance(last_verdict_at, datetime):
        return None
    if last_verdict_at.tzinfo is not None:
        last_verdict_at = last_verdict_at.replace(tzinfo=None)
    current = now if now is not None else datetime.utcnow()
    return max(0.0, (current - last_verdict_at).total_seconds() / 3600.0)


def build_alert_text(
    *,
    age_hours: float,
    max_age_hours: float,
    backlog: int,
    dashboard_url: Optional[str] = None,
) -> str:
    """Текст алёрта. Backlog в сообщении — это цена простоя, а не украшение."""
    parts = [
        "⚠️ <b>SETKA: ИИ-фильтр молчит</b>\n",
        f"Последний вердикт был <b>{age_hours:.1f} ч</b> назад (порог {max_age_hours:g} ч).",
        f"\nБез разметки накопилось <b>{backlog}</b> постов в окне свежести —"
        " они уходят в сводки без фильтра нейросети.",
        "\n\nЧастая причина — ключ DeepSeek не доехал из комнаты КАРМАНа при"
        " старте воркера. Проверь: <code>journalctl -u setka-celery-worker |"
        " grep vault</code> и <code>grep 'classifier headless'"
        " logs/celery-worker.log</code>.",
    ]
    if dashboard_url:
        parts.append(f"\n🔗 <a href='{dashboard_url}'>Открыть ленту вердиктов</a>")
    return "\n".join(parts)


def maybe_alert_stale_classifier(
    *,
    last_verdict_at: Optional[Any],
    backlog: int = 0,
    max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
    telegram_token: Optional[str] = None,
    chat_id: Optional[str] = None,
    dashboard_url: Optional[str] = None,
    now: Optional[datetime] = None,
) -> str:
    """Алёрт, если вердиктов давно нет. Возвращает статус-строку.

    ``fresh`` | ``unknown:no-verdicts`` | ``skipped:no-telegram-config`` |
    ``skipped:cooldown`` | ``alert-sent`` | ``error:…``
    """
    age = verdict_age_hours(last_verdict_at, now=now)
    if age is None:
        return "unknown:no-verdicts"
    if age < max_age_hours:
        return "fresh"

    if not telegram_token or not chat_id:
        logger.error(
            "classifier heartbeat: вердиктов нет %.1f ч, но алёрт послать некуда"
            " (нет TELEGRAM_ALERT_CHAT_ID/токена)",
            age,
        )
        return "skipped:no-telegram-config"

    client = _redis()
    try:
        if client is not None and client.get(COOLDOWN_KEY):
            return "skipped:cooldown"
    except Exception:  # noqa: BLE001 — cooldown не критичен
        pass

    message = build_alert_text(
        age_hours=age,
        max_age_hours=max_age_hours,
        backlog=backlog,
        dashboard_url=dashboard_url,
    )
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
            logger.warning(
                "classifier stale alert failed: %s %s", resp.status_code, resp.text[:200]
            )
            return "error:http-" + str(resp.status_code)
        if client is not None:
            client.setex(COOLDOWN_KEY, ALERT_COOLDOWN_SECONDS, "1")
        logger.warning("classifier heartbeat: алёрт отправлен, возраст вердикта %.1f ч", age)
        return "alert-sent"
    except Exception as exc:  # noqa: BLE001
        logger.error("classifier stale alert failed: %s", exc)
        return "error:" + str(exc)


__all__ = [
    "ALERT_COOLDOWN_SECONDS",
    "DEFAULT_MAX_AGE_HOURS",
    "build_alert_text",
    "maybe_alert_stale_classifier",
    "verdict_age_hours",
]
