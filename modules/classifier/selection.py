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
from typing import Any, Dict, Optional, Set, Tuple

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


async def fetch_publish_map(session, region_code: str) -> Dict[str, Optional[str]]:
    """lip → канон-тема для постов, которые нейро-вердикт РАЗРЕШАЕТ в сводку.

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
        from modules.classifier.service import _theme_canon_map, canonicalize_theme

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
                    ClassificationCorrection.verdict_type,
                    ClassificationCorrection.operator_value,
                ).where(
                    ClassificationCorrection.classification_id.in_([r[0] for r in rows]),
                    ClassificationCorrection.verdict_type.in_(("action", "theme")),
                    ClassificationCorrection.outcome == "correct",
                )
            )
        ).all()
        operator_action = {cid: val for cid, vtype, val in corr_rows if vtype == "action"}
        operator_theme = {cid: val for cid, vtype, val in corr_rows if vtype == "theme"}

        canon = await _theme_canon_map(session)
        allowed: Dict[str, Optional[str]] = {}
        for cid, lip, verdict in rows:
            action = operator_action.get(cid)
            if action is None:
                action = ((verdict or {}).get("action") or "").strip().lower()
            else:
                action = str(action or "").strip().lower()
            if action != "publish":
                continue
            # Тема — тем же правилом, что и действие: правка человека главнее
            # решения ИИ. Раньше здесь читалось только `action`, поэтому тема
            # вердикта на отбор не влияла вовсе — и запрещённая рубрика ехала в
            # ленту, если у поста стоял publish.
            raw_theme = operator_theme.get(cid)
            if raw_theme is None:
                raw_theme = (verdict or {}).get("theme")
            allowed[lip] = canonicalize_theme(raw_theme, canon)
        return allowed
    except Exception as e:  # noqa: BLE001 — наблюдаемость не роняет публикацию
        logger.warning("classifier selection: чтение вердиктов не удалось: %s", e)
        return {}


async def fetch_publish_lips(session, region_code: str) -> Set[str]:
    """Только lip'ы, разрешённые вердиктом. Тонкая обёртка над ``fetch_publish_map``.

    Осталась отдельной функцией, потому что у неё есть свой потребитель
    (``modules.classifier.rating.top_by_rating``), которому тема не нужна, и
    десяток тестов, подменяющих её монкейпатчем.
    """
    return set(await fetch_publish_map(session, region_code))


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


MODE_DISABLED = "disabled"  # гейт выключен — волна идёт как раньше


def selection_enabled() -> bool:
    """Отбирать ли в сводку по меткам (env, дефолт — выкл; зеркало prepublish)."""
    import os

    return os.getenv("CLASSIFIER_SELECTION_ENABLED", "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


async def _fetch_theme_shares(session) -> Dict[str, Optional[float]]:
    """Доли тем из словаря: ``{тема: процент}``. Служебные темы пропускаем.

    «Мусор» не публикуется вовсе, «соседи» идут отдельным каналом со своим
    расписанием — потолок для них был бы ручкой, ни к чему не подключённой.
    """
    from sqlalchemy import select

    from database.models_extended import ClassifierTheme

    rows = (
        await session.execute(
            select(
                ClassifierTheme.name,
                ClassifierTheme.share_percent,
                ClassifierTheme.is_service,
            )
        )
    ).all()
    return {
        str(name): (None if share is None else float(share))
        for name, share, is_service in rows
        if not is_service
    }


async def _apply_quota(
    session,
    selected,
    *,
    region_code: str,
    theme_of,
) -> Tuple[list, Dict[str, int]]:
    """Применить потолки долей тем. Fail-open: любой отказ → вход без изменений.

    Запрет темы (доля 0) работает ВСЕГДА, а потолки — только под гейтом
    ``CLASSIFIER_THEME_QUOTA_ENABLED``. Разделение нарочное: гейт нужен, чтобы
    журнал публикаций сутки поработал вхолостую и знаменатель перестал быть
    пустым, но запрет владельца ждать сутки не должен.
    """
    # Без сессии читать нечего: так зовут отбор юнит-тесты политики деградации,
    # и ERROR-лог в этом случае был бы ложной тревогой ровно того класса, который
    # приучает не читать логи.
    if not selected or session is None:
        return selected, {}
    try:
        from config.classifier import (
            get_theme_quota_min_posts,
            get_theme_quota_window_hours,
            theme_quota_enabled,
        )
        from modules.classifier.quota import HEADLINER_SLOTS, apply_theme_quota
        from modules.publication_journal import fetch_published_counts

        shares = await _fetch_theme_shares(session)
        if not theme_quota_enabled():
            shares = {t: v for t, v in shares.items() if v is not None and float(v) <= 0}
        shares = {t: v for t, v in shares.items() if v is not None}
        if not shares:
            return selected, {}

        published: Dict[str, int] = {}
        slots = HEADLINER_SLOTS
        if theme_quota_enabled():
            published = await fetch_published_counts(
                session, region_code, window_hours=get_theme_quota_window_hours()
            )
            # Мест в ленте, которые добавляет эта волна: сводка плюс хедлайнер.
            # Берём сетевой дефолт, а не эффективное значение региона: конфиг
            # региона сюда не приезжает, а расхождение стоит долей процента в
            # знаменателе — меньше, чем цена лишнего параметра в сигнатуре,
            # которую пришлось бы протащить через обе точки вызова.
            from modules.bulletin_pipeline_settings import DEFAULT_PIPELINE

            slots = int(DEFAULT_PIPELINE.get("max_posts_per_bulletin", 3)) + HEADLINER_SLOTS

        from config.classifier import get_rating_views_alpha
        from utils.post_utils import post_rating_of

        alpha = get_rating_views_alpha()
        return apply_theme_quota(
            selected,
            theme_of=theme_of,
            rating_of=lambda p: post_rating_of(p, alpha=alpha),
            shares=shares,
            published=published,
            slots=slots,
            min_posts=get_theme_quota_min_posts(),
        )
    except Exception as e:  # noqa: BLE001 — квота не важнее волны
        logger.error("theme quota failed — fail-open: %s", e, exc_info=True)
        return selected, {}


async def apply_wave_selection(
    session,
    posts,
    *,
    region_code: str,
    theme: str,
) -> Tuple[list, str, int]:
    """Единственная точка входа отбора для волны публикации (звено 5, шаг 2).

    Возвращает ``(посты, режим, сколько_убрано)``. Порядок в волне жёсткий:
    вызывается ПОСЛЕ prepublish — тот доклассифицирует свежесобранное и
    записывает вердикты, которые здесь читает ``fetch_publish_lips``. Обратный
    порядок оставил бы свежие посты без вердиктов, и отбор «только publish»
    выбрасывал бы их все.

    Redis и Telegram-конфиг добываются здесь, а не пробрасываются через волну:
    отказ любого из них деградирует политику (см. ``decide_mode``/``maybe_alert``),
    но не меняет сигнатуру вызова. Любой НЕполитический отказ — fail-open с
    ERROR: усилитель, не точка отказа (тот же контракт, что у prepublish).
    """
    if not selection_enabled() or not posts:
        return list(posts), MODE_DISABLED, 0

    try:
        from modules.classifier.prepublish import post_lip

        publish_map = await fetch_publish_map(session, region_code)
        publish_lips = set(publish_map)

        redis_client = None
        try:
            from modules.notifications.storage import NotificationsStorage

            redis_client = NotificationsStorage().redis_client
        except Exception:  # noqa: BLE001 — без Redis политика деградирует сама
            logger.debug("classifier selection: redis unavailable", exc_info=True)

        mode, need_alert = decide_mode(
            publish_lips=publish_lips,
            region_code=region_code,
            theme=theme,
            redis_client=redis_client,
        )

        if need_alert:
            try:
                from config.runtime import TELEGRAM_ALERT_CHAT_ID, TELEGRAM_TOKENS

                # Та же эвристика, что _pick_telegram_bot_token в celery_app;
                # импортировать её оттуда нельзя — модуль не должен тянуть
                # celery-приложение с его import-side-effects.
                tokens = dict(TELEGRAM_TOKENS)
                bot_token = next(
                    (tokens[k] for k in ("VALSTANBOT", "ALERT", "AFONYA") if tokens.get(k)),
                    next(iter(tokens.values()), None),
                )
                status = maybe_alert(
                    mode=mode,
                    region_code=region_code,
                    theme=theme,
                    telegram_token=bot_token,
                    chat_id=TELEGRAM_ALERT_CHAT_ID,
                    redis_client=redis_client,
                )
                logger.warning(
                    "classifier selection: фильтр молчит (%s/%s), режим=%s, алёрт=%s",
                    region_code,
                    theme,
                    mode,
                    status,
                )
            except Exception:  # noqa: BLE001 — алёрт не важнее волны
                logger.error("classifier selection: алёрт не отправлен", exc_info=True)

        if mode == MODE_SKIP_WAVE:
            return [], mode, len(posts)
        if mode == MODE_FALLBACK:
            return list(posts), mode, 0

        selected = [p for p in posts if post_lip(p) in publish_lips]
        removed = len(posts) - len(selected)

        # Квота тем (заказ владельца 2026-08-30). Врезана здесь, а не выше или
        # ниже по волне, по трём причинам: сигнатура apply_wave_selection не
        # меняется, поэтому обе точки вызова — районная волна и каскад — получают
        # квоту без правок; только здесь на руках одновременно посты и их темы
        # (ниже, в BulletinBuilder, темы уже нет и сессии БД тоже); и это уже
        # слой редакционных решений, а не алгоритмических фильтров.
        selected, quota_dropped = await _apply_quota(
            session,
            selected,
            region_code=region_code,
            theme_of=lambda p: publish_map.get(post_lip(p)),
        )
        removed += sum(quota_dropped.values())
        # Сколько из убранных движок не судил вовсе. Пост без текста headless
        # пропускает намеренно (`has_text`, headless.py:85), вердикта у него не
        # появляется — а отбор берёт ТОЛЬКО publish, и «нет вердикта» молча
        # значит «не публиковать». В вычитающем мире такой пост публиковался.
        # Без этого числа `отобрано=0` неразличимо: то ли нейросеть отбраковала
        # всё, то ли ей нечего было судить. Замер 2026-08-21: 56% пустых волн —
        # второе. Берём ИМЕННО `has_text` движка, а не свою копию условия:
        # разошедшиеся определения — самый незаметный способ мерить не то.
        from modules.classifier.headless import has_text

        no_text = sum(1 for p in posts if post_lip(p) not in publish_lips and not has_text(p))
        logger.info(
            "classifier selection: регион=%s тема=%s кандидатов=%d отобрано=%d"
            " убрано=%d безтекста=%d квота=%s",
            region_code,
            theme,
            len(posts),
            len(selected),
            removed,
            no_text,
            # Пишем в ту же строку, что и режим отбора: квота живёт под гейтом
            # CLASSIFIER_SELECTION_ENABLED, и её молчаливое отключение иначе
            # выглядело бы точно так же, как «квота ничего не срезала».
            quota_dropped or "не применялась",
        )
        return selected, mode, removed
    except Exception as e:  # noqa: BLE001 — усилитель, не точка отказа
        logger.error("classifier selection failed — fail-open: %s", e, exc_info=True)
        return list(posts), "error", 0
