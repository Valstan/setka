"""Отбор в сводку ПО МЕТКАМ нейросети + политика деградации (звено 5, D-024).

**Смена смысла вердикта.** До сих пор вердикт работал вычитанием:
``enforce.fetch_blocked_lips`` убирал ``delete``/``hold`` из кандидатов, а сам
отбор вели алгоритмические фильтры. Метка ``publish`` не значила «взять» — она
значила «не мешать». Здесь наоборот: в сводку идут ТОЛЬКО посты с ``publish``,
а редакционные фильтры отключаются как второй, слепой редактор.

**Зачем меняем.** Измерено 2026-08-12: из 2313 постов, отброшенных алгоритмами,
ИИ-фильтр считает публикуемыми 995 (43%) — фильтр ``advertisement`` резал
«Продам помидоры» и «Продаю свежий мёд», ровно то, что утверждённые правила 16,
22 и 26 велят публиковать. Два редактора с противоположными инструкциями, и
слепой побеждал, потому что стоял первым. Обратная сторона важнее: алгоритмы
пропускают спам, за который блокируют аккаунт владельца (бан до 18.08.2026).

**Политика деградации — решение владельца, а не общий приём.** Если вердиктов
на регион нет (DeepSeek лёг, ключ кончился, сайт провайдера упал):

1. **Первая волна — пропускается.** Ничего не публикуем и ждём.
2. **Вторая волна подряд — публикуем алгоритмами**, но ТОЛЬКО её саму.
   Пропущенная волна не досылается: два поста подряд в ленту — хуже, чем один
   пропуск. Догонять упущенное нельзя.
3. Первая же успешная волна сбрасывает счётчик.

**Молчание нейросети — событие, о котором сообщают.** Не «тихо переживём»:
владелец должен узнать, что фильтр встал, и разобраться (кончились токены, лёг
провайдер, отозван ключ). Поэтому пропуск волны шлёт Telegram-алёрт с cooldown.

Состояние волн живёт в Redis по ключу ``(регион, тема)``: волны идут по
расписанию тем, и «вторая волна подряд» считается внутри своей темы — у
``novost`` и ``afisha`` разные календари, смешивать их счётчики значит
пропускать публикации там, где всё в порядке.
"""

from __future__ import annotations

import logging
from typing import Any, Optional, Set, Tuple

logger = logging.getLogger(__name__)

KEY_PREFIX = "setka:classifier_selection"
# Сколько живёт отметка «волна пропущена». Больше суток держать бессмысленно:
# если фильтр молчал сутки, это уже не «пропустим волну», а инцидент.
_SKIP_TTL_SECONDS = 26 * 3600
# Не спамить алёртом на каждой волне, пока инцидент длится.
ALERT_COOLDOWN_SECONDS = 6 * 3600

MODE_VERDICTS = "verdicts"  # отбор по меткам — норма
MODE_SKIP_WAVE = "skip-wave"  # вердиктов нет, первая волна — молчим
MODE_FALLBACK = "algorithmic-fallback"  # вердиктов нет вторую волну — алгоритмы


async def fetch_publish_lips(session, region_code: str) -> Set[str]:
    """lip'ы, которые нейро-вердикт РАЗРЕШАЕТ в сводку региона.

    Зеркало ``enforce.fetch_blocked_lips`` с той же семантикой правки оператора
    (``verdict_type='action'``, ``outcome='correct'``) — правка человека главнее
    решения ИИ в обе стороны. Окно свежести то же, что у источника
    классификатора: посты старше него в сводку и так не идут.

    Fail-closed НЕ делаем: ошибка чтения → пустой набор, а вызывающий по пустому
    набору уйдёт в политику деградации (пропуск волны + алёрт). Тихо
    опубликовать всё подряд при сломанной БД — ровно тот сценарий, из-за
    которого аккаунт улетает в бан.
    """
    from datetime import datetime, timedelta

    from sqlalchemy import select

    try:
        from config.classifier import get_source_days
        from database.models_extended import ClassificationCorrection, ContentClassification

        cutoff = datetime.utcnow() - timedelta(days=get_source_days())
        rows = (
            await session.execute(
                select(
                    ContentClassification.id,
                    ContentClassification.lip,
                    ContentClassification.verdict,
                ).where(
                    ContentClassification.created_at >= cutoff,
                    ContentClassification.region_code == region_code,
                )
            )
        ).all()
        if not rows:
            return set()

        corr_rows = (
            await session.execute(
                select(
                    ClassificationCorrection.classification_id,
                    ClassificationCorrection.operator_value,
                ).where(
                    ClassificationCorrection.classification_id.in_([r[0] for r in rows]),
                    ClassificationCorrection.verdict_type == "action",
                    ClassificationCorrection.outcome == "correct",
                )
            )
        ).all()
        operator_action = {cid: val for cid, val in corr_rows}

        allowed: Set[str] = set()
        for cid, lip, verdict in rows:
            action = operator_action.get(cid)
            if action is None:
                action = ((verdict or {}).get("action") or "").strip().lower()
            else:
                action = str(action or "").strip().lower()
            if action == "publish":
                allowed.add(lip)
        return allowed
    except Exception as e:  # noqa: BLE001 — наблюдаемость не роняет публикацию
        logger.warning("classifier selection: чтение вердиктов не удалось: %s", e)
        return set()


def _skip_key(region_code: str, theme: str) -> str:
    return f"{KEY_PREFIX}:skipped:{region_code}:{theme}"


def _alert_key(region_code: str) -> str:
    return f"{KEY_PREFIX}:alert_cooldown:{region_code}"


def decide_mode(
    *,
    publish_lips: Set[str],
    region_code: str,
    theme: str,
    redis_client: Any = None,
) -> Tuple[str, bool]:
    """Выбрать режим волны. Возвращает ``(режим, слать_ли_алёрт)``.

    Вердикты есть → ``verdicts`` и сброс отметки пропуска.
    Вердиктов нет, отметки нет → ``skip-wave``, ставим отметку, алёрт.
    Вердиктов нет, отметка есть → ``algorithmic-fallback``, отметку снимаем
    (следующий отказ снова начнёт с пропуска волны — политика повторяется, а не
    залипает в режиме алгоритмов навсегда).

    Без Redis отметку хранить негде: тогда единственное безопасное поведение —
    пропустить волну и сообщить. Молча уйти в алгоритмы значит выпустить в ленту
    то, за что банят аккаунт.
    """
    if publish_lips:
        if redis_client is not None:
            try:
                redis_client.delete(_skip_key(region_code, theme))
            except Exception:  # noqa: BLE001
                pass
        return MODE_VERDICTS, False

    if redis_client is None:
        logger.warning(
            "classifier selection: вердиктов нет и Redis недоступен (%s/%s) — пропускаем волну",
            region_code,
            theme,
        )
        return MODE_SKIP_WAVE, True

    try:
        already_skipped = bool(redis_client.get(_skip_key(region_code, theme)))
    except Exception:  # noqa: BLE001
        already_skipped = False

    if already_skipped:
        try:
            redis_client.delete(_skip_key(region_code, theme))
        except Exception:  # noqa: BLE001
            pass
        logger.warning(
            "classifier selection: вердиктов нет вторую волну (%s/%s) — публикуем алгоритмами, "
            "пропущенную волну НЕ досылаем",
            region_code,
            theme,
        )
        return MODE_FALLBACK, True

    try:
        redis_client.setex(_skip_key(region_code, theme), _SKIP_TTL_SECONDS, 1)
    except Exception:  # noqa: BLE001
        pass
    return MODE_SKIP_WAVE, True


def format_alert(*, mode: str, region_code: str, theme: str) -> str:
    """Текст Telegram-алёрта о молчании ИИ-фильтра."""
    if mode == MODE_SKIP_WAVE:
        head = "⚠️ <b>ИИ-фильтр молчит — волна пропущена</b>"
        tail = (
            "Волна <b>не опубликована</b>. Если на следующей волне вердиктов снова не будет, "
            "сводка выйдет на алгоритмических фильтрах — а они пропускают спам."
        )
    else:
        head = "🚨 <b>ИИ-фильтр молчит вторую волну — публикуем алгоритмами</b>"
        tail = (
            "Сводка выйдет на алгоритмических фильтрах. Пропущенная волна НЕ досылается. "
            "Алгоритмы пропускают спам — разберись со фильтром."
        )
    return "\n".join(
        [
            head,
            "",
            f"Регион: <b>{region_code}</b>, тема: <b>{theme}</b>",
            "",
            "Что проверить: кончились ли токены DeepSeek, жив ли их API, "
            "не отозван ли ключ в комнате КАРМАНа.",
            "",
            tail,
        ]
    )


def maybe_alert(
    *,
    mode: str,
    region_code: str,
    theme: str,
    telegram_token: Optional[str],
    chat_id: Optional[str],
    redis_client: Any = None,
) -> str:
    """Послать алёрт о молчании фильтра (с cooldown по региону).

    Cooldown по РЕГИОНУ, а не по паре регион+тема: если фильтр встал, он встал
    для всех тем сразу, и отдельное письмо на каждую волну каждой темы —
    гарантированный способ научить владельца не читать эти алёрты.
    """
    if not telegram_token or not chat_id:
        return "skipped:no-telegram-config"
    if redis_client is not None:
        try:
            if redis_client.get(_alert_key(region_code)):
                return "skipped:cooldown"
        except Exception:  # noqa: BLE001
            pass
    try:
        import requests

        resp = requests.post(
            f"https://api.telegram.org/bot{telegram_token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": format_alert(mode=mode, region_code=region_code, theme=theme),
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
        if resp.status_code != 200:
            return f"error:http-{resp.status_code}"
    except Exception as e:  # noqa: BLE001
        return f"error:{type(e).__name__}"

    if redis_client is not None:
        try:
            redis_client.setex(_alert_key(region_code), ALERT_COOLDOWN_SECONDS, 1)
        except Exception:  # noqa: BLE001
            pass
    return "alert-sent"
