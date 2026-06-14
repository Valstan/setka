"""
Celery Application

Главное приложение Celery для автоматизации SETKA.

Tasks:
- run_vk_monitoring: Запуск production workflow каждый час
- create_daily_digest: Создание дайджеста за день (18:00)
- cleanup_old_posts: Очистка старых постов (03:00)

Запуск:
    # Worker
    celery -A tasks.celery_app worker --loglevel=info

    # Beat scheduler
    celery -A tasks.celery_app beat --loglevel=info
"""

# Добавляем корневую директорию в PYTHONPATH — должно быть ДО любых проектных
# импортов, иначе flake8 ругается E402 на нижестоящие `from utils...` / `from
# config...`. Делаем самый минимум на верху, остальные импорты — ниже.
import hashlib
import json
import logging
from datetime import datetime, timedelta

from celery import Celery, signals
from celery.schedules import crontab

from utils.celery_asyncio import run_coro
from utils.json_logging import configure_json_logging

# Setup logging
# Plain-text по умолчанию (формат как раньше). При env LOG_FORMAT=json форматтер
# переустанавливается на структурированный JSON (см. utils/json_logging.py) —
# удобно грепать/парсить логи worker'а на инцидентах. Повторно дёргаем из
# worker_ready-хука ниже, т.к. Celery переинициализирует логгеры при старте.
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s - %(message)s")
configure_json_logging()
logger = logging.getLogger(__name__)

# --- Telegram alerts for Notifications ---


def _pick_telegram_bot_token(telegram_tokens: dict) -> str | None:
    # Prefer historically used names, then fall back to any configured token.
    for key in ("VALSTANBOT", "ALERT", "AFONYA"):
        token = telegram_tokens.get(key)
        if token:
            return token
    # Any token is better than none
    return next(iter(telegram_tokens.values()), None)


def _compute_notifications_signature(payload: dict) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _maybe_send_telegram_notifications_alert() -> None:
    """
    Send Telegram alert if there are any notifications and the payload is NEW.

    Triggered from `check_recent_comments` (last task in the hourly chain),
    so that suggested/messages/comments are aggregated into a single alert.
    """
    try:
        import requests

        from config.runtime import TELEGRAM_ALERT_CHAT_ID, TELEGRAM_TOKENS
        from modules.notifications.storage import NotificationsStorage

        storage = NotificationsStorage()
        data = storage.get_all_notifications()

        # Nothing to notify about.
        if (data.get("total_count") or 0) <= 0:
            return

        bot_token = _pick_telegram_bot_token(TELEGRAM_TOKENS)
        chat_id = TELEGRAM_ALERT_CHAT_ID
        if not bot_token or not chat_id:
            logger.warning("Telegram credentials not configured; skipping notifications alert")
            return

        # Dedupe: do not spam the same alert every hour.
        signature_payload = {
            "suggested_posts": data.get("suggested_posts", []),
            "unread_messages": data.get("unread_messages", []),
            "recent_comments": data.get("recent_comments", []),
        }
        signature = _compute_notifications_signature(signature_payload)
        last_sig_key = f"{storage.key_prefix}:last_telegram_signature"
        last_sig = storage.redis_client.get(last_sig_key)
        if last_sig == signature:
            return
        storage.redis_client.setex(last_sig_key, 86400, signature)

        suggested = data.get("suggested_posts") or []
        messages = data.get("unread_messages") or []
        comments = data.get("recent_comments") or []

        # Build a compact message (HTML).
        lines: list[str] = []
        lines.append("<b>📬 Новые уведомления SETKA</b>")
        lines.append("")
        lines.append(f"📝 Предложенных постов: <b>{len(suggested)}</b>")
        for n in suggested[:5]:
            name = n.get("region_name", "?")
            cnt = n.get("suggested_count", 0)
            url = n.get("url", "")
            if url:
                lines.append(f"  • {name}: {cnt} — <a href='{url}'>проверить</a>")
            else:
                lines.append(f"  • {name}: {cnt}")
        if len(suggested) > 5:
            lines.append(f"  …и ещё {len(suggested) - 5} регион(ов)")

        lines.append("")
        lines.append(f"💬 Непрочитанных сообщений: <b>{len(messages)}</b>")
        for n in messages[:5]:
            name = n.get("region_name", "?")
            cnt = n.get("unread_count", 0)
            url = n.get("url", "")
            if url:
                lines.append(f"  • {name}: {cnt} — <a href='{url}'>открыть</a>")
            else:
                lines.append(f"  • {name}: {cnt}")
        if len(messages) > 5:
            lines.append(f"  …и ещё {len(messages) - 5} регион(ов)")

        lines.append("")
        lines.append(f"💭 Комментариев за сутки: <b>{len(comments)}</b>")
        for c in comments[:5]:
            name = c.get("region_name", "?")
            text = (c.get("text") or "").strip().replace("<", "&lt;").replace(">", "&gt;")
            post_url = c.get("post_url", "")
            preview = (text[:120] + "…") if len(text) > 120 else text
            if post_url:
                lines.append(f"  • {name}: {preview} — <a href='{post_url}'>пост</a>")
            else:
                lines.append(f"  • {name}: {preview}")
        if len(comments) > 5:
            lines.append(f"  …и ещё {len(comments) - 5} комментариев")

        message = "\n".join(lines)

        resp = requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
        if resp.status_code != 200:
            logger.warning(f"Telegram sendMessage failed: {resp.status_code} {resp.text[:300]}")
        else:
            logger.info("Telegram notifications alert sent")

    except Exception as e:
        logger.warning(f"Failed to send Telegram notifications alert: {e}")


# Создаем Celery app
# IMPORTANT: keep a single Celery runtime and explicitly include tasks that are scheduled by beat.
app = Celery(
    "setka",
    include=[
        "tasks.parsing_tasks",
        "tasks.parsing_scheduler_tasks",  # Postopus migration
        "tasks.discovery_tasks",  # community discovery + weekly recheck
        "tasks.radar_tasks",  # content radar: fan-out source poller (Ф0.2)
        "tasks.broadcast_tasks",  # сетевая рассылка: диспетчер-публикатор (brain 2026-06-14)
    ],
)
app.config_from_object("config.celery_config")


# При выставленной ``PROMETHEUS_MULTIPROC_DIR`` worker пишет метрики в shared
# mmap-файл с именем по своему PID. Если процесс умер и mmap не подчистить,
# его counter'ы продолжат участвовать в агрегациях через MultiProcessCollector.
# ``mark_process_dead`` исключает PID из выдачи. Защита идемпотентна — если
# env-var не выставлена, prometheus_client поднимет ValueError, мы его глотаем.
@signals.worker_shutdown.connect  # type: ignore[has-type]
def _setka_mark_prom_process_dead(**_kwargs) -> None:
    import os

    if not os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        return
    try:
        from prometheus_client import multiprocess

        multiprocess.mark_process_dead(os.getpid())
    except Exception:
        logger.debug("prometheus mark_process_dead failed", exc_info=True)


# Celery переинициализирует логирование при старте worker'а (хватает root-логгер
# через свой ``setup_logging`` / ``--loglevel``), затирая форматтер, выставленный
# на import-е модуля. Переустанавливаем JSON-форматтер уже после готовности
# worker'а, чтобы LOG_FORMAT=json реально действовал на его выводе.
@signals.worker_ready.connect  # type: ignore[has-type]
def _setka_apply_json_logging(**_kwargs) -> None:
    try:
        if configure_json_logging():
            logger.info("JSON logging enabled for Celery worker (LOG_FORMAT=json)")
    except Exception:
        logger.debug("configure_json_logging failed", exc_info=True)


@app.task(name="tasks.celery_app.run_vk_monitoring")
def run_vk_monitoring():
    """
    Запуск production workflow для мониторинга VK.

    Выполняется каждый час в :05 минут.
    Сканирует все активные регионы, применяет фильтры,
    делает AI scoring и создает дайджесты.
    """
    logger.info("=" * 80)
    logger.info("Starting VK monitoring workflow...")
    logger.info("=" * 80)

    try:
        from scripts.run_production_workflow import ProductionWorkflow

        # Создаем и запускаем workflow
        workflow = ProductionWorkflow()
        result = run_coro(workflow.run())

        logger.info("VK monitoring completed successfully!")
        logger.info(f"Result: {result}")

        return {"success": True, "timestamp": datetime.now().isoformat(), "result": result}

    except Exception as e:
        logger.error(f"VK monitoring failed: {e}", exc_info=True)
        return {"success": False, "timestamp": datetime.now().isoformat(), "error": str(e)}


@app.task(name="tasks.celery_app.create_daily_digest")
def create_daily_digest():
    """
    Создание дневного дайджеста для всех регионов.

    Выполняется каждый день в 18:00.
    Собирает топ-посты за день, создает дайджесты,
    готовит к публикации.
    """
    logger.info("=" * 80)
    logger.info("Creating daily digest...")
    logger.info("=" * 80)

    try:
        from sqlalchemy import and_, select

        from database.connection import AsyncSessionLocal
        from database.models import Post, Region
        from modules.aggregation.aggregator import NewsAggregator

        async def create_digest():
            async with AsyncSessionLocal() as session:
                # Получаем все активные регионы
                result = await session.execute(select(Region).where(Region.is_active.is_(True)))
                regions = list(result.scalars())

                aggregator = NewsAggregator(session)
                digests = []

                # Создаем дайджест для каждого региона
                for region in regions:
                    logger.info(f"Creating digest for {region.name}...")

                    # Получаем посты за последние 24 часа
                    cutoff_time = datetime.now() - timedelta(hours=24)
                    posts_result = await session.execute(
                        select(Post)
                        .where(
                            and_(
                                Post.region_id == region.id,
                                Post.date_published >= cutoff_time,
                                Post.ai_analyzed.is_(True),
                            )
                        )
                        .order_by(Post.ai_score.desc())
                        .limit(10)
                    )
                    posts = list(posts_result.scalars())

                    if not posts:
                        logger.warning(f"No posts found for {region.name}")
                        continue

                    # Создаем дайджест
                    digest = await aggregator.create_digest(posts=posts, region=region, max_posts=5)

                    if digest:
                        digests.append(
                            {
                                "region": region.name,
                                "posts_count": len(digest.source_posts),
                                "total_views": digest.total_views,
                                "text_length": len(digest.aggregated_text),
                            }
                        )
                        logger.info(
                            f"Digest created for {region.name}: {len(digest.source_posts)} posts"
                        )

                return digests

        digests = run_coro(create_digest())

        logger.info(f"Daily digest completed! Created {len(digests)} digests")

        return {"success": True, "timestamp": datetime.now().isoformat(), "digests": digests}

    except Exception as e:
        logger.error(f"Daily digest failed: {e}", exc_info=True)
        return {"success": False, "timestamp": datetime.now().isoformat(), "error": str(e)}


@app.task(name="tasks.celery_app.check_suggested_posts")
def check_suggested_posts():
    """
    Проверка предложенных постов в главных группах регионов.

    Выполняется каждый час с 8:00 до 22:00.
    Проверяет все главные группы регионов (с префиксом ИНФО) на наличие
    предложенных постов от посетителей.

    Результаты сохраняются в Redis и отправляются в Telegram.
    """
    logger.info("=" * 80)
    logger.info("Checking suggested posts in region groups...")
    logger.info("=" * 80)

    try:
        from sqlalchemy import select

        from config.runtime import VK_TOKENS
        from database.connection import AsyncSessionLocal
        from database.models import Region
        from modules.notifications.storage import NotificationsStorage
        from modules.notifications.vk_suggested_checker import VKSuggestedChecker
        from modules.vk_token_router import load_community_tokens

        async def check():
            # Получаем все регионы с главными группами
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    select(Region).where(
                        Region.vk_group_id.isnot(None),
                        # Уведомления должны проверяться независимо от статуса "пауза" региона.
                    )
                )
                regions = list(result.scalars())

                if not regions:
                    logger.warning("No regions with VK groups found")
                    return []

                logger.info(f"Checking {len(regions)} region groups...")

                # Подготавливаем данные для проверки
                region_groups = [
                    {
                        "region_id": r.id,
                        "region_name": r.name,
                        "region_code": r.code,
                        "vk_group_id": r.vk_group_id,
                    }
                    for r in regions
                ]

                # Проверяем предложенные посты
                vk_token = VK_TOKENS.get("VALSTAN")
                if not vk_token:
                    logger.error("VK token not found")
                    return []

                community_tokens = await load_community_tokens(session)
                checker = VKSuggestedChecker(vk_token, community_tokens=community_tokens)
                run_start = datetime.now()
                notifications = await checker.check_all_region_groups(region_groups)
                run_duration = (datetime.now() - run_start).total_seconds()

                # Сохраняем в Redis. keep_if_empty=True защищает от стирания
                # результата ручной проверки, если автопроверка вернула 0
                # из-за временной ошибки VK API (например, сломаны
                # community-tokens).
                storage = NotificationsStorage()
                storage.save_notifications(
                    notifications,
                    "suggested_posts",
                    keep_if_empty=True,
                )
                # История проверок (этап 3): для виджета «активность за 24ч».
                storage.save_run(
                    "suggested_posts",
                    count=len(notifications),
                    duration_seconds=run_duration,
                    success=True,
                )
                # Prometheus (etap 5)
                try:
                    from monitoring.metrics import (
                        notifications_check_duration_seconds,
                        notifications_check_total,
                        notifications_items_found_total,
                    )

                    result_label = "ok" if notifications else "empty"
                    notifications_check_total.labels(
                        check_type="suggested",
                        result=result_label,
                    ).inc()
                    notifications_check_duration_seconds.labels(
                        check_type="suggested",
                    ).observe(run_duration)
                    notifications_items_found_total.labels(
                        check_type="suggested",
                    ).inc(len(notifications))
                except Exception as _e:
                    logger.debug("metrics emit failed: %s", _e)

                logger.info(f"Found {len(notifications)} groups with suggested posts")

                return notifications

        notifications = run_coro(check())

        return {
            "success": True,
            "timestamp": datetime.now().isoformat(),
            "notifications_count": len(notifications),
            "notifications": notifications,
        }

    except Exception as e:
        logger.error(f"Failed to check suggested posts: {e}", exc_info=True)
        return {"success": False, "timestamp": datetime.now().isoformat(), "error": str(e)}


@app.task(name="tasks.celery_app.scan_suggested_ads")
def scan_suggested_ads():
    """Скан предложки на рекламу → заявки в ad_requests (рекламный кабинет).

    Каждые 30 минут 8:00-22:00 (на 25-й минуте — офсет от notification-задач).
    Детект через AdvertisementFilter + предложка-сигналы, дедуп по
    (community_vk_id, vk_post_id). Telegram-алерт только при НОВЫХ заявках
    (new_total>0 — дедуп уже на уровне БД, повторных алертов не будет).
    """
    logger.info("=" * 80)
    logger.info("Scanning предложка for advertisements (ad cabinet)...")
    logger.info("=" * 80)

    try:
        from modules.ad_cabinet.scanner import run_scan

        result = run_coro(run_scan())
        new_total = int(result.get("new_total", 0))
        if new_total > 0:
            _maybe_alert_new_ads(new_total, result.get("regions", []))

        logger.info("ad cabinet scan done: %d new ad requests", new_total)
        return {
            "success": result.get("success", False),
            "timestamp": datetime.now().isoformat(),
            "new_total": new_total,
            "regions": result.get("regions", []),
        }
    except Exception as e:
        logger.error(f"scan_suggested_ads failed: {e}", exc_info=True)
        return {"success": False, "timestamp": datetime.now().isoformat(), "error": str(e)}


def _maybe_alert_new_ads(new_total: int, regions: list, source_label: str = "предложке") -> None:
    """Telegram-алерт о новых рекламных заявках (best-effort).

    ``source_label`` — откуда заявки (``"предложке"`` / ``"личке"``), для текста.
    """
    try:
        import requests as _requests

        from config.runtime import SERVER, TELEGRAM_ALERT_CHAT_ID, TELEGRAM_TOKENS

        token = TELEGRAM_TOKENS.get("VALSTANBOT") or TELEGRAM_TOKENS.get("ALERT")
        chat_id = TELEGRAM_ALERT_CHAT_ID
        if not token or not chat_id:
            return
        domain = (
            SERVER.get("domain") or f"{SERVER.get('host', '127.0.0.1')}:{SERVER.get('port', 8000)}"
        )
        url = f"https://{domain}/ad"
        by_region = ", ".join(
            f"{r.get('region_code')}:{r.get('new')}" for r in (regions or []) if r.get("new")
        )
        text = (
            f"📢 Новых рекламных заявок в {source_label}: <b>{new_total}</b>\n"
            f"{by_region}\n\n{url}"
        )
        _requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
    except Exception as e:
        logger.warning(f"ad cabinet telegram alert failed: {e}")


def _send_debtor_alert(text: str) -> None:
    """Отправить Telegram-напоминание о должниках (best-effort, С4)."""
    try:
        import requests as _requests

        from config.runtime import TELEGRAM_ALERT_CHAT_ID, TELEGRAM_TOKENS

        token = TELEGRAM_TOKENS.get("VALSTANBOT") or TELEGRAM_TOKENS.get("ALERT")
        chat_id = TELEGRAM_ALERT_CHAT_ID
        if not token or not chat_id:
            return
        _requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
    except Exception as e:
        logger.warning(f"ad debtor telegram alert failed: {e}")


@app.task(name="tasks.celery_app.scan_inbound_dm_ads")
def scan_inbound_dm_ads():
    """Скан входящих ЛС сообществ на рекламу → заявки в ad_requests (блок A).

    Каждые 30 минут 8:00-22:00 (на 35-й/05-й минуте — офсет от скана предложки
    в X:25/55). Источник — ``messages.getConversations`` главных групп; детект
    тем же AdvertisementFilter + предложка-сигналы; дедуп по (community_vk_id,
    peer_id) при origin='inbound_dm'. Telegram-алерт только при НОВЫХ заявках.
    """
    logger.info("=" * 80)
    logger.info("Scanning inbound DM for advertisements (ad cabinet, block A)...")
    logger.info("=" * 80)

    try:
        from modules.ad_cabinet.dm_scanner import run_dm_scan

        result = run_coro(run_dm_scan())
        new_total = int(result.get("new_total", 0))
        # Telegram-алерт — только про НОВУЮ рекламу в личке; не-рекламные ЛС видны в
        # разделе «Уведомления» (Этап 1), отдельно ими не спамим.
        new_ads_total = int(result.get("new_ads_total", 0))
        if new_ads_total > 0:
            _maybe_alert_new_ads(new_ads_total, result.get("regions", []), source_label="личке")

        logger.info(
            "ad cabinet DM scan done: %d new DM rows (%d ad → cabinet)",
            new_total,
            new_ads_total,
        )
        return {
            "success": result.get("success", False),
            "timestamp": datetime.now().isoformat(),
            "new_total": new_total,
            "new_ads_total": new_ads_total,
            "regions": result.get("regions", []),
        }
    except Exception as e:
        logger.error(f"scan_inbound_dm_ads failed: {e}", exc_info=True)
        return {"success": False, "timestamp": datetime.now().isoformat(), "error": str(e)}


@app.task(name="tasks.celery_app.reconcile_scheduled_publications")
def reconcile_scheduled_publications():
    """Авто-фиксация публикаций отложки (рекламный кабинет, PR-6).

    Каждые 30 минут 8:00-22:00 (X:45). Для отложек, чьё время прошло, проверяет
    через VK факт публикации и фиксирует в CRM: статус→published, AdPublication,
    awaiting-оплата (если есть client+price), событие в таймлайн. Идемпотентно.
    """
    logger.info("Reconciling scheduled ad publications (ad cabinet, PR-6)...")
    try:
        from modules.ad_cabinet.publish_reconciler import run_reconcile

        result = run_coro(run_reconcile())
        logger.info(
            "reconcile scheduled publications done: %s",
            result,
        )
        return {"success": True, "timestamp": datetime.now().isoformat(), **result}
    except Exception as e:
        logger.error(f"reconcile_scheduled_publications failed: {e}", exc_info=True)
        return {"success": False, "timestamp": datetime.now().isoformat(), "error": str(e)}


@app.task(name="tasks.celery_app.expire_ad_posts")
def expire_ad_posts():
    """Авто-снятие рекламных постов по истечении срока (С2, ad-CRM).

    Раз в сутки (03:30 MSK). Для вышедших публикаций с истёкшим expires_at —
    wall.delete + статус removed + removed_at + событие 'removed' в таймлайн.
    Срок опционален (без срока пост висит вечно). Идемпотентно (только
    status='published').
    """
    logger.info("Expiring ad posts past their term (ad cabinet, С2)...")
    try:
        from modules.ad_cabinet.post_expirer import run_expiry

        result = run_coro(run_expiry())
        logger.info("expire ad posts done: %s", result)
        return {"success": True, "timestamp": datetime.now().isoformat(), **result}
    except Exception as e:
        logger.error(f"expire_ad_posts failed: {e}", exc_info=True)
        return {"success": False, "timestamp": datetime.now().isoformat(), "error": str(e)}


@app.task(name="tasks.celery_app.collect_ad_publication_stats")
def collect_ad_publication_stats():
    """Суточный сбор метрик рекламных публикаций (С3, ad-CRM).

    Раз в сутки (04:30 MSK). Для вышедших публикаций тянет просмотры/лайки/
    репосты через wall.getById и пишет снимок. Ручное обновление одного клиента —
    через API-кнопку (run_collect_stats(only_client_id=...)).
    """
    logger.info("Collecting ad publication stats (ad cabinet, С3)...")
    try:
        from modules.ad_cabinet.publication_stats import run_collect_stats

        result = run_coro(run_collect_stats())
        logger.info("collect ad publication stats done: %s", result)
        return {"success": True, "timestamp": datetime.now().isoformat(), **result}
    except Exception as e:
        logger.error(f"collect_ad_publication_stats failed: {e}", exc_info=True)
        return {"success": False, "timestamp": datetime.now().isoformat(), "error": str(e)}


@app.task(name="tasks.celery_app.alert_ad_debtors")
def alert_ad_debtors():
    """Суточное Telegram-напоминание о должниках (С4, ad-CRM).

    Раз в день (10:00 MSK). Клиенты с awaiting-оплатами старше порога (3 дн.,
    AD_DEBTOR_DAYS) → один Telegram-список оператору. Полу-авто: оплату оператор
    отмечает руками, код лишь напоминает о просрочке.
    """
    logger.info("Alerting ad debtors (ad cabinet, С4)...")
    try:
        from config.runtime import SERVER
        from modules.ad_cabinet.debtors import run_debtor_alert

        domain = (
            SERVER.get("domain") or f"{SERVER.get('host', '127.0.0.1')}:{SERVER.get('port', 8000)}"
        )
        url = f"https://{domain}/ad#crm"
        result = run_coro(run_debtor_alert(send=_send_debtor_alert, url=url))
        logger.info("ad debtors alert done: %s", result)
        return {"success": True, "timestamp": datetime.now().isoformat(), **result}
    except Exception as e:
        logger.error(f"alert_ad_debtors failed: {e}", exc_info=True)
        return {"success": False, "timestamp": datetime.now().isoformat(), "error": str(e)}


@app.task(name="tasks.celery_app.auto_greet_ad_requests")
def auto_greet_ad_requests():
    """Авто-приветствие рекламодателю на новую заявку (улучшение отклика).

    Каждые 30 мин 8:00-22:00 (X:10/40, сразу после сканов). Свежим новым заявкам
    в разрешённых сообществах (env AD_AUTO_GREETING_COMMUNITIES) шлёт приветствие
    один раз. Off по умолчанию (пустой allowlist → no-op).
    """
    logger.info("Auto-greeting new ad requests (ad cabinet)...")
    try:
        from modules.ad_cabinet.auto_greeting import run_auto_greeting

        result = run_coro(run_auto_greeting())
        logger.info("auto-greeting done: %s", result)
        return {"success": True, "timestamp": datetime.now().isoformat(), **result}
    except Exception as e:
        logger.error(f"auto_greet_ad_requests failed: {e}", exc_info=True)
        return {"success": False, "timestamp": datetime.now().isoformat(), "error": str(e)}


@app.task(name="tasks.celery_app.collect_member_snapshots")
def collect_member_snapshots():
    """Суточный снимок подписчиков ГЛАВНЫХ ИНФО-групп активных регионов (04:00 MSK).

    Тянет `groups.getById(fields=members_count)` по `regions.vk_group_id` и пишет
    по строке на (регион, день) в `region_member_snapshots` — фундамент графика
    роста подписчиков. Только главные группы (куда выпускаем дайджесты), не весь
    пул источников — экономия VK API. Идемпотентно за день (upsert).
    """
    logger.info("Collecting region member-count snapshots...")
    try:
        from modules.members_snapshot import collect_member_snapshots as _collect

        result = run_coro(_collect())
        logger.info("member snapshots done: %s", result)
        return {"success": True, "timestamp": datetime.now().isoformat(), **result}
    except Exception as e:
        logger.error(f"collect_member_snapshots failed: {e}", exc_info=True)
        return {"success": False, "timestamp": datetime.now().isoformat(), "error": str(e)}


@app.task(name="tasks.celery_app.collect_oblast_unique_snapshots")
def collect_oblast_unique_snapshots():
    """Еженедельный снимок УНИКАЛЬНЫХ подписчиков области без дублей (ночь пн).

    Объединяет member-id главных ИНФО-групп каждой области (сама область +
    районы) через `groups.getMembers` и пишет уникальных в
    `oblast_unique_member_snapshots` — «чистый» охват области для сравнения
    областей на графике роста. Только ~16 главных групп (1000 id/запрос) →
    нагрузка ничтожна. Идемпотентно за день (upsert).
    """
    logger.info("Collecting oblast unique-member snapshots...")
    try:
        from modules.oblast_unique_members import collect_oblast_unique_snapshots as _collect

        result = run_coro(_collect())
        logger.info("oblast unique snapshots done: %s", result)
        return {"success": True, "timestamp": datetime.now().isoformat(), **result}
    except Exception as e:
        logger.error(f"collect_oblast_unique_snapshots failed: {e}", exc_info=True)
        return {"success": False, "timestamp": datetime.now().isoformat(), "error": str(e)}


@app.task(name="tasks.celery_app.check_unread_messages")
def check_unread_messages():
    """Проверка непрочитанных сообщений в главных группах регионов.

    Окно 8:00-22:00 MSK гарантируется beat-расписанием
    `crontab(minute=16, hour='8-22')` в `beat_schedule`. Внутри таски доп.
    проверка часа не нужна — раньше она лишь дублировала фильтр.
    """
    logger.info("=" * 80)
    logger.info("Checking unread messages in region groups...")
    logger.info("=" * 80)

    try:
        from sqlalchemy import select

        from config.runtime import VK_TOKENS
        from database.connection import AsyncSessionLocal
        from database.models import Region
        from modules.notifications.storage import NotificationsStorage
        from modules.notifications.vk_messages_checker import VKMessagesChecker
        from modules.vk_token_router import load_community_tokens

        async def check():
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    select(Region).where(
                        Region.vk_group_id.isnot(None),
                        # Уведомления должны проверяться независимо от статуса "пауза" региона.
                    )
                )
                regions = list(result.scalars())

                if not regions:
                    logger.warning("No regions with VK groups found")
                    return []

                logger.info(f"Checking {len(regions)} region groups for unread messages...")

                region_groups = [
                    {
                        "region_id": r.id,
                        "region_name": r.name,
                        "region_code": r.code,
                        "vk_group_id": r.vk_group_id,
                    }
                    for r in regions
                ]

                vk_token = VK_TOKENS.get("VALSTAN")
                if not vk_token:
                    logger.error("VK token not found")
                    return []

                # Community-токены для каждой группы (если есть) — checker предпочтёт их.
                community_tokens = await load_community_tokens(session)

                checker = VKMessagesChecker(vk_token, community_tokens=community_tokens)
                run_start = datetime.now()
                result = await checker.check_all_region_groups(region_groups)
                run_duration = (datetime.now() - run_start).total_seconds()
                notifications = result["notifications"]
                denied_groups = result["denied_groups"]

                storage = NotificationsStorage()
                storage.save_notifications(notifications, "unread_messages")
                storage.save_notifications(denied_groups, "unread_messages_denied")
                storage.save_run(
                    "unread_messages",
                    count=len(notifications),
                    denied_count=len(denied_groups),
                    duration_seconds=run_duration,
                    success=True,
                )
                try:
                    from monitoring.metrics import (
                        notifications_check_duration_seconds,
                        notifications_check_total,
                        notifications_items_found_total,
                    )

                    if denied_groups and not notifications:
                        result_label = "denied"
                    elif notifications:
                        result_label = "ok"
                    else:
                        result_label = "empty"
                    notifications_check_total.labels(
                        check_type="messages",
                        result=result_label,
                    ).inc()
                    notifications_check_duration_seconds.labels(
                        check_type="messages",
                    ).observe(run_duration)
                    notifications_items_found_total.labels(
                        check_type="messages",
                    ).inc(len(notifications))
                except Exception as _e:
                    logger.debug("metrics emit failed: %s", _e)

                logger.info(
                    "Found %d groups with unread messages (%d denied access)",
                    len(notifications),
                    len(denied_groups),
                )
                return notifications, denied_groups

        notifications, denied_groups = run_coro(check())

        return {
            "success": True,
            "timestamp": datetime.now().isoformat(),
            "notifications_count": len(notifications),
            "notifications": notifications,
            "denied_count": len(denied_groups),
        }

    except Exception as e:
        logger.error(f"Failed to check unread messages: {e}", exc_info=True)
        return {"success": False, "timestamp": datetime.now().isoformat(), "error": str(e)}


@app.task(name="tasks.celery_app.check_recent_comments")
def check_recent_comments():
    """Проверка комментариев за последние 24 часа в главных ИНФО-группах регионов.

    Окно 8:00-22:00 MSK гарантируется beat-расписанием
    `crontab(minute=17, hour='8-22')` в `beat_schedule`. Доп. inside-task
    проверка часа удалена (этап 2 рефактора).
    """
    from datetime import datetime, timedelta

    logger.info("=" * 80)
    logger.info("Checking recent comments (last 24h) under posts of all communities...")
    logger.info("=" * 80)

    try:
        from sqlalchemy import select

        from config.runtime import VK_TOKENS
        from database.connection import AsyncSessionLocal
        from database.models import Region
        from modules.notifications.storage import NotificationsStorage
        from modules.notifications.vk_comments_checker import VKCommentsChecker
        from modules.vk_token_router import load_community_tokens

        cutoff_dt = datetime.utcnow() - timedelta(hours=24)
        cutoff_ts = int(cutoff_dt.timestamp())

        async def check():
            vk_token = VK_TOKENS.get("VALSTAN")
            if not vk_token:
                logger.error("VK token not found")
                return []

            async with AsyncSessionLocal() as session:
                # Берём только главные ИНФО-группы регионов
                rows = await session.execute(
                    select(Region.id, Region.code, Region.name, Region.vk_group_id)
                    .where(
                        # Уведомления должны проверяться независимо от статуса "пауза" региона.
                        Region.vk_group_id.isnot(None),
                    )
                    .order_by(Region.id)
                )
                regions = rows.all()

                region_groups = []
                for region_id, region_code, region_name, vk_group_id in regions:
                    # Дополнительный фильтр по префиксу/маркеру ИНФО
                    if "ИНФО" not in (region_name or ""):
                        continue
                    region_groups.append(
                        {
                            "region_id": region_id,
                            "region_code": region_code,
                            "region_name": region_name,
                            "vk_group_id": vk_group_id,
                        }
                    )

                community_tokens = await load_community_tokens(session)
                checker = VKCommentsChecker(vk_token, community_tokens=community_tokens)
                run_start = datetime.now()
                notifications = await checker.check_recent_comments_for_region_groups(
                    region_groups=region_groups, cutoff_ts=cutoff_ts
                )
                run_duration = (datetime.now() - run_start).total_seconds()

                # keep_if_empty=True: ручной запуск из UI мог только что обнаружить
                # комментарии — не стираем их если автотаска вернула 0 из-за
                # community-token error 27.
                storage = NotificationsStorage()
                storage.save_notifications(
                    notifications,
                    "recent_comments",
                    keep_if_empty=True,
                )
                storage.save_run(
                    "recent_comments",
                    count=len(notifications),
                    duration_seconds=run_duration,
                    success=True,
                )
                try:
                    from monitoring.metrics import (
                        notifications_check_duration_seconds,
                        notifications_check_total,
                        notifications_items_found_total,
                    )

                    result_label = "ok" if notifications else "empty"
                    notifications_check_total.labels(
                        check_type="comments",
                        result=result_label,
                    ).inc()
                    notifications_check_duration_seconds.labels(
                        check_type="comments",
                    ).observe(run_duration)
                    notifications_items_found_total.labels(
                        check_type="comments",
                    ).inc(len(notifications))
                except Exception as _e:
                    logger.debug("metrics emit failed: %s", _e)

                logger.info(f"Found {len(notifications)} recent comments (main INFO groups only)")
                return notifications

        notifications = run_coro(check())

        # После обновления всех ключей (suggested/messages/comments) отправляем агрегированное
        # Telegram-уведомление (если есть новые элементы).
        _maybe_send_telegram_notifications_alert()

        # Health watchdog (этап 5): если последние N автопроверок подряд
        # вернули 0 — намёк на сломанный токен, шлём отдельный alert
        # (с собственным cooldown, чтобы не спамить).
        try:
            from config.runtime import SERVER, TELEGRAM_ALERT_CHAT_ID, TELEGRAM_TOKENS
            from modules.notifications.health import maybe_alert_broken_tokens

            telegram_token = TELEGRAM_TOKENS.get("VALSTANBOT")
            chat_id = TELEGRAM_ALERT_CHAT_ID
            domain = (
                SERVER.get("domain")
                or f"{SERVER.get('host', '127.0.0.1')}:{SERVER.get('port', 8000)}"
            )
            dashboard_url = f"https://{domain}/notifications"

            run_coro(
                maybe_alert_broken_tokens(
                    telegram_token=telegram_token,
                    chat_id=chat_id,
                    dashboard_url=dashboard_url,
                )
            )
        except Exception as _e:
            logger.debug("token health watchdog failed: %s", _e)

        return {
            "success": True,
            "timestamp": datetime.now().isoformat(),
            "comments_count": len(notifications),
            "notifications": notifications,
        }

    except Exception as e:
        logger.error(f"Failed to check recent comments: {e}", exc_info=True)
        return {"success": False, "timestamp": datetime.now().isoformat(), "error": str(e)}


@app.task(name="tasks.celery_app.check_digest_heartbeat")
def check_digest_heartbeat():
    """Watchdog «давно нет дайджестов»: алёрт, если novost давно не публиковался.

    Читает Redis-heartbeat (пишется из ``track_digest_published`` на всех путях
    публикации). Если тема ``novost`` протухла дольше порога — Telegram-алёрт
    с собственным cooldown (см. ``modules.digest_heartbeat``). Beat гоняет днём
    (10:00–22:00); ночью простой 20:40→6:40 легитимен и не проверяется.
    """
    try:
        from config.runtime import SERVER, TELEGRAM_ALERT_CHAT_ID, TELEGRAM_TOKENS
        from modules.digest_heartbeat import maybe_alert_stale_digest

        token = TELEGRAM_TOKENS.get("VALSTANBOT") or TELEGRAM_TOKENS.get("ALERT")
        chat_id = TELEGRAM_ALERT_CHAT_ID
        domain = (
            SERVER.get("domain") or f"{SERVER.get('host', '127.0.0.1')}:{SERVER.get('port', 8000)}"
        )
        status = maybe_alert_stale_digest(
            topic="novost",
            telegram_token=token,
            chat_id=chat_id,
            dashboard_url=f"https://{domain}/",
        )
        logger.info("digest heartbeat watchdog: %s", status)
        return {"success": True, "status": status, "timestamp": datetime.now().isoformat()}
    except Exception as e:
        logger.error(f"check_digest_heartbeat failed: {e}", exc_info=True)
        return {"success": False, "timestamp": datetime.now().isoformat(), "error": str(e)}


@app.task(name="tasks.celery_app.cleanup_old_posts")
def cleanup_old_posts():
    """
    Очистка старых постов из БД.

    Выполняется каждый день в 03:00.
    Удаляет посты старше 30 дней для освобождения места.
    """
    logger.info("=" * 80)
    logger.info("Cleaning up old posts...")
    logger.info("=" * 80)

    try:
        from sqlalchemy import delete

        from database.connection import AsyncSessionLocal
        from database.models import Post

        async def cleanup():
            async with AsyncSessionLocal() as session:
                # Удаляем посты старше 30 дней
                cutoff_date = datetime.now() - timedelta(days=30)

                result = await session.execute(
                    delete(Post).where(Post.date_published < cutoff_date)
                )

                deleted_count = result.rowcount
                await session.commit()

                return deleted_count

        deleted_count = run_coro(cleanup())

        logger.info(f"Cleanup completed! Deleted {deleted_count} old posts")

        return {
            "success": True,
            "timestamp": datetime.now().isoformat(),
            "deleted_count": deleted_count,
        }

    except Exception as e:
        logger.error(f"Cleanup failed: {e}", exc_info=True)
        return {"success": False, "timestamp": datetime.now().isoformat(), "error": str(e)}


# Расписания (Beat Schedule)
app.conf.beat_schedule = {
    # Проверка предложенных постов каждый час с 8:00 до 22:00 в X:15
    "check-suggested-hourly": {
        "task": "tasks.celery_app.check_suggested_posts",
        "schedule": crontab(minute=15, hour="8-22"),  # Каждый час 8-22 на 15-й минуте
        "options": {
            "expires": 3000,
            "catchup": False,
        },
    },
    # Скан предложки на рекламу (рекламный кабинет) каждые 30 мин 8:00-22:00 в X:25/55
    "scan-suggested-ads": {
        "task": "tasks.celery_app.scan_suggested_ads",
        "schedule": crontab(minute="25,55", hour="8-22"),
        "options": {
            "expires": 1500,
            "catchup": False,
        },
    },
    # Скан входящих ЛС на рекламу (рекламный кабинет, блок A) каждые 30 мин в X:05/35
    "scan-inbound-dm-ads": {
        "task": "tasks.celery_app.scan_inbound_dm_ads",
        "schedule": crontab(minute="5,35", hour="8-22"),
        "options": {
            "expires": 1500,
            "catchup": False,
        },
    },
    # Авто-приветствие рекламодателю на новую заявку — X:10/40 (сразу после сканов).
    # Off по умолчанию (env AD_AUTO_GREETING_COMMUNITIES пуст → no-op).
    "auto-greet-ad-requests": {
        "task": "tasks.celery_app.auto_greet_ad_requests",
        "schedule": crontab(minute="10,40", hour="8-22"),
        "options": {
            "expires": 1500,
            "catchup": False,
        },
    },
    # Авто-фиксация публикаций отложки (рекламный кабинет, PR-6) каждые 30 мин в X:45
    "reconcile-scheduled-publications": {
        "task": "tasks.celery_app.reconcile_scheduled_publications",
        "schedule": crontab(minute="45", hour="8-22"),
        "options": {
            "expires": 1500,
            "catchup": False,
        },
    },
    # Авто-снятие рекламных постов по истечении срока (С2, ad-CRM) — 03:30 MSK
    # (после cleanup 03:00 и radar-retention 03:20). Срок опционален.
    "expire-ad-posts-daily": {
        "task": "tasks.celery_app.expire_ad_posts",
        "schedule": crontab(minute=30, hour=3),
        "options": {
            "expires": 3600,
            "catchup": False,
        },
    },
    # Суточное напоминание о должниках по рекламе (С4, ad-CRM) — 10:00 MSK
    "alert-ad-debtors-daily": {
        "task": "tasks.celery_app.alert_ad_debtors",
        "schedule": crontab(minute=0, hour=10),
        "options": {
            "expires": 3600,
            "catchup": False,
        },
    },
    # Суточный сбор метрик рекламных публикаций (С3, ad-CRM) — 04:30 MSK
    "collect-ad-publication-stats-daily": {
        "task": "tasks.celery_app.collect_ad_publication_stats",
        "schedule": crontab(minute=30, hour=4),
        "options": {
            "expires": 3600,
            "catchup": False,
        },
    },
    # Суточный снимок числа подписчиков сообществ (фундамент графика роста) — 04:00 MSK
    "collect-member-snapshots-daily": {
        "task": "tasks.celery_app.collect_member_snapshots",
        "schedule": crontab(minute=0, hour=4),
        "options": {
            "expires": 3600,
            "catchup": False,
        },
    },
    # Еженедельный снимок УНИКАЛЬНЫХ подписчиков области без дублей — пн 02:30 UTC
    # (05:30 MSK), пока сеть спит. groups.getMembers по ~16 главным группам дёшев.
    "collect-oblast-unique-snapshots-weekly": {
        "task": "tasks.celery_app.collect_oblast_unique_snapshots",
        "schedule": crontab(minute=30, hour=2, day_of_week=1),
        "options": {
            "expires": 6 * 3600,
            "catchup": False,
        },
    },
    # Проверка непрочитанных сообщений каждый час с 8:00 до 22:00 в X:16
    "check-unread-messages-hourly": {
        "task": "tasks.celery_app.check_unread_messages",
        "schedule": crontab(minute=16, hour="8-22"),  # Каждый час 8-22 на 16-й минуте
        "options": {
            "expires": 3000,
            "catchup": False,
        },
    },
    # Проверка комментариев за сутки каждый час с 8:00 до 22:00 в X:17
    "check-recent-comments-hourly": {
        "task": "tasks.celery_app.check_recent_comments",
        "schedule": crontab(minute=17, hour="8-22"),  # Каждый час 8-22 на 17-й минуте
        "options": {
            "expires": 3000,
            "catchup": False,
        },
    },
    # Дневной дайджест в 18:00
    "digest-daily": {
        "task": "tasks.celery_app.create_daily_digest",
        "schedule": crontab(hour=18, minute=0),  # 18:00 каждый день
        "options": {
            "expires": 3000,
            "catchup": False,
        },
    },
    # Очистка старых постов в 03:00
    "cleanup-daily": {
        "task": "tasks.celery_app.cleanup_old_posts",
        "schedule": crontab(hour=3, minute=0),  # 03:00 каждый день
        "options": {
            "expires": 3000,
            "catchup": False,
        },
    },
    # Watchdog «давно нет дайджестов»: раз в час 10:00–22:00 на :05. Если тема
    # novost не публиковалась дольше порога (6ч в самой задаче) — Telegram-алёрт
    # (с 6ч-cooldown). novost-волны идут 6×/сутки (макс дневной зазор ~5ч), порог
    # 6ч с запасом. Ночью не гоняем — простой 20:40→6:40 легитимен.
    "digest-heartbeat-watchdog": {
        "task": "tasks.celery_app.check_digest_heartbeat",
        "schedule": crontab(minute=5, hour="10-22"),
        "options": {
            "expires": 1800,
            "catchup": False,
        },
    },
    # ========================================================================
    # POSTOPUS MIGRATION: Crontab replacement → Celery Beat
    # Original crontab entries migrated from old_postopus
    # ========================================================================
    # Reklama (ads): 5 10,14,19 * * *
    "postopus-reklama-10": {
        "task": "tasks.parsing_scheduler_tasks.run_all_regions_theme",
        "schedule": crontab(minute=5, hour=10),
        "args": ("reklama",),
        "options": {"expires": 3600},
    },
    "postopus-reklama-14": {
        "task": "tasks.parsing_scheduler_tasks.run_all_regions_theme",
        "schedule": crontab(minute=5, hour=14),
        "args": ("reklama",),
        "options": {"expires": 3600},
    },
    "postopus-reklama-19": {
        "task": "tasks.parsing_scheduler_tasks.run_all_regions_theme",
        "schedule": crontab(minute=5, hour=19),
        "args": ("reklama",),
        "options": {"expires": 3600},
    },
    # Sosed (neighbor news): 15 10,20 * * *
    "postopus-sosed-10": {
        "task": "tasks.parsing_scheduler_tasks.run_all_regions_theme",
        "schedule": crontab(minute=20, hour=10),
        "args": ("sosed",),
        "options": {"expires": 3600},
    },
    "postopus-sosed-20": {
        "task": "tasks.parsing_scheduler_tasks.run_all_regions_theme",
        "schedule": crontab(minute=20, hour=20),
        "args": ("sosed",),
        "options": {"expires": 3600},
    },
    # Соседский обмен новостями (cross-region): раз в сутки утром. Каждый регион
    # с непустым Region.neighbors репостит #Новости с главных групп соседей.
    # Это НЕ тема "sosed" выше (та — парсинг сообществ category="sosed" внутри
    # региона). Движок — modules.cascaded_digest.run_neighbor_digest.
    "digest-share-neighbors-daily": {
        "task": "tasks.parsing_scheduler_tasks.run_all_regions_neighbor_share",
        "schedule": crontab(minute=30, hour=8),
        "options": {"expires": 3600},
    },
    # Novost (news): 40 6,11,12,16,18,20 * * *
    "postopus-novost-6": {
        "task": "tasks.parsing_scheduler_tasks.run_all_regions_theme",
        "schedule": crontab(minute=40, hour=6),
        "args": ("novost",),
        "options": {"expires": 3600},
    },
    "postopus-novost-11": {
        "task": "tasks.parsing_scheduler_tasks.run_all_regions_theme",
        "schedule": crontab(minute=40, hour=11),
        "args": ("novost",),
        "options": {"expires": 3600},
    },
    "postopus-novost-12": {
        "task": "tasks.parsing_scheduler_tasks.run_all_regions_theme",
        "schedule": crontab(minute=40, hour=12),
        "args": ("novost",),
        "options": {"expires": 3600},
    },
    "postopus-novost-16": {
        "task": "tasks.parsing_scheduler_tasks.run_all_regions_theme",
        "schedule": crontab(minute=40, hour=16),
        "args": ("novost",),
        "options": {"expires": 3600},
    },
    "postopus-novost-18": {
        "task": "tasks.parsing_scheduler_tasks.run_all_regions_theme",
        "schedule": crontab(minute=40, hour=18),
        "args": ("novost",),
        "options": {"expires": 3600},
    },
    "postopus-novost-20": {
        "task": "tasks.parsing_scheduler_tasks.run_all_regions_theme",
        "schedule": crontab(minute=40, hour=20),
        "args": ("novost",),
        "options": {"expires": 3600},
    },
    # ───────────────────────────────────────────────────────────────────────
    # Областные тематические волны (community-mode oblast, 2026-05).
    # kirov_obl перешёл с каскада (дайджест-дайджестов из районов) на свой пул
    # communities по темам — публикует тематические дайджесты как район.
    # Базовые темы (novost/sport/kultura/admin) область получает на ОБЩИХ волнах
    # postopus-<theme>-* (после снятия хардкод-исключения kirov_obl). Ниже —
    # волны под расширенную областную повестку. strict=True: волна берёт только
    # регионы с communities ИМЕННО этой темы (районы не затрагиваются — у них
    # таких сообществ нет). Слоты разнесены 7:30–22:00 на :50/:10 (свободны от
    # :40/:45/:20/:30 базовых тем). Старые postopus-kirov-oblast-* (каскад) сняты.
    "postopus-proisshestviya-8": {
        "task": "tasks.parsing_scheduler_tasks.run_all_regions_theme",
        "schedule": crontab(minute=50, hour=8),
        "kwargs": {"theme": "proisshestviya", "strict": True},
        "options": {"expires": 3600},
    },
    "postopus-proisshestviya-14": {
        "task": "tasks.parsing_scheduler_tasks.run_all_regions_theme",
        "schedule": crontab(minute=50, hour=14),
        "kwargs": {"theme": "proisshestviya", "strict": True},
        "options": {"expires": 3600},
    },
    "postopus-proisshestviya-20": {
        "task": "tasks.parsing_scheduler_tasks.run_all_regions_theme",
        "schedule": crontab(minute=50, hour=20),
        "kwargs": {"theme": "proisshestviya", "strict": True},
        "options": {"expires": 3600},
    },
    "postopus-zhkh-7": {
        "task": "tasks.parsing_scheduler_tasks.run_all_regions_theme",
        "schedule": crontab(minute=50, hour=7),
        "kwargs": {"theme": "zhkh", "strict": True},
        "options": {"expires": 3600},
    },
    "postopus-zhkh-15": {
        "task": "tasks.parsing_scheduler_tasks.run_all_regions_theme",
        "schedule": crontab(minute=50, hour=15),
        "kwargs": {"theme": "zhkh", "strict": True},
        "options": {"expires": 3600},
    },
    "postopus-selhoz-9": {
        "task": "tasks.parsing_scheduler_tasks.run_all_regions_theme",
        "schedule": crontab(minute=50, hour=9),
        "kwargs": {"theme": "selhoz", "strict": True},
        "options": {"expires": 3600},
    },
    "postopus-selhoz-16": {
        "task": "tasks.parsing_scheduler_tasks.run_all_regions_theme",
        "schedule": crontab(minute=50, hour=16),
        "kwargs": {"theme": "selhoz", "strict": True},
        "options": {"expires": 3600},
    },
    "postopus-nauka-10": {
        "task": "tasks.parsing_scheduler_tasks.run_all_regions_theme",
        "schedule": crontab(minute=50, hour=10),
        "kwargs": {"theme": "nauka", "strict": True},
        "options": {"expires": 3600},
    },
    "postopus-nauka-17": {
        "task": "tasks.parsing_scheduler_tasks.run_all_regions_theme",
        "schedule": crontab(minute=50, hour=17),
        "kwargs": {"theme": "nauka", "strict": True},
        "options": {"expires": 3600},
    },
    "postopus-promyshlennost-11": {
        "task": "tasks.parsing_scheduler_tasks.run_all_regions_theme",
        "schedule": crontab(minute=50, hour=11),
        "kwargs": {"theme": "promyshlennost", "strict": True},
        "options": {"expires": 3600},
    },
    "postopus-promyshlennost-19": {
        "task": "tasks.parsing_scheduler_tasks.run_all_regions_theme",
        "schedule": crontab(minute=50, hour=19),
        "kwargs": {"theme": "promyshlennost", "strict": True},
        "options": {"expires": 3600},
    },
    "postopus-molodezh-12": {
        "task": "tasks.parsing_scheduler_tasks.run_all_regions_theme",
        "schedule": crontab(minute=50, hour=12),
        "kwargs": {"theme": "molodezh", "strict": True},
        "options": {"expires": 3600},
    },
    "postopus-molodezh-18": {
        "task": "tasks.parsing_scheduler_tasks.run_all_regions_theme",
        "schedule": crontab(minute=50, hour=18),
        "kwargs": {"theme": "molodezh", "strict": True},
        "options": {"expires": 3600},
    },
    "postopus-zdorovie-13": {
        "task": "tasks.parsing_scheduler_tasks.run_all_regions_theme",
        "schedule": crontab(minute=50, hour=13),
        "kwargs": {"theme": "zdorovie", "strict": True},
        "options": {"expires": 3600},
    },
    "postopus-zdorovie-21": {
        "task": "tasks.parsing_scheduler_tasks.run_all_regions_theme",
        "schedule": crontab(minute=50, hour=21),
        "kwargs": {"theme": "zdorovie", "strict": True},
        "options": {"expires": 3600},
    },
    "postopus-priroda-12": {
        "task": "tasks.parsing_scheduler_tasks.run_all_regions_theme",
        "schedule": crontab(minute=10, hour=12),
        "kwargs": {"theme": "priroda", "strict": True},
        "options": {"expires": 3600},
    },
    "postopus-priroda-19": {
        "task": "tasks.parsing_scheduler_tasks.run_all_regions_theme",
        "schedule": crontab(minute=10, hour=19),
        "kwargs": {"theme": "priroda", "strict": True},
        "options": {"expires": 3600},
    },
    # Татарстан (областной каскадный дайджест из главных групп bal/kukmor).
    # Детей всего 2 — 2 слота/сутки достаточно (чаще = повторы). minute=45 как
    # у kirov-oblast (после волн novost :40). Публикует при наличии
    # community-токена COMM_239149826 (см. миграцию 016).
    "postopus-tatarstan-oblast-9": {
        "task": "tasks.parsing_scheduler_tasks.parse_and_publish_theme",
        "schedule": crontab(minute=45, hour=9),
        "kwargs": {"region_code": "tatarstan_obl", "theme": "oblast"},
        "options": {"expires": 3600},
    },
    "postopus-tatarstan-oblast-19": {
        "task": "tasks.parsing_scheduler_tasks.parse_and_publish_theme",
        "schedule": crontab(minute=45, hour=19),
        "kwargs": {"region_code": "tatarstan_obl", "theme": "oblast"},
        "options": {"expires": 3600},
    },
    # Kultura (culture): 20 7,13,16,19,21 * * *
    "postopus-kultura-7": {
        "task": "tasks.parsing_scheduler_tasks.run_all_regions_theme",
        "schedule": crontab(minute=20, hour=7),
        "args": ("kultura",),
        "options": {"expires": 3600},
    },
    "postopus-kultura-13": {
        "task": "tasks.parsing_scheduler_tasks.run_all_regions_theme",
        "schedule": crontab(minute=20, hour=13),
        "args": ("kultura",),
        "options": {"expires": 3600},
    },
    "postopus-kultura-16": {
        "task": "tasks.parsing_scheduler_tasks.run_all_regions_theme",
        "schedule": crontab(minute=20, hour=16),
        "args": ("kultura",),
        "options": {"expires": 3600},
    },
    "postopus-kultura-19": {
        "task": "tasks.parsing_scheduler_tasks.run_all_regions_theme",
        "schedule": crontab(minute=20, hour=19),
        "args": ("kultura",),
        "options": {"expires": 3600},
    },
    "postopus-kultura-21": {
        "task": "tasks.parsing_scheduler_tasks.run_all_regions_theme",
        "schedule": crontab(minute=20, hour=21),
        "args": ("kultura",),
        "options": {"expires": 3600},
    },
    # Sport: 30 12,19 * * *
    "postopus-sport-12": {
        "task": "tasks.parsing_scheduler_tasks.run_all_regions_theme",
        "schedule": crontab(minute=30, hour=12),
        "args": ("sport",),
        "options": {"expires": 3600},
    },
    "postopus-sport-19": {
        "task": "tasks.parsing_scheduler_tasks.run_all_regions_theme",
        "schedule": crontab(minute=30, hour=19),
        "args": ("sport",),
        "options": {"expires": 3600},
    },
    # Admin: 20 8,12,20 * * *
    "postopus-admin-8": {
        "task": "tasks.parsing_scheduler_tasks.run_all_regions_theme",
        "schedule": crontab(minute=20, hour=8),
        "args": ("admin",),
        "options": {"expires": 3600},
    },
    "postopus-admin-12": {
        "task": "tasks.parsing_scheduler_tasks.run_all_regions_theme",
        "schedule": crontab(minute=20, hour=12),
        "args": ("admin",),
        "options": {"expires": 3600},
    },
    "postopus-admin-20": {
        "task": "tasks.parsing_scheduler_tasks.run_all_regions_theme",
        "schedule": crontab(minute=20, hour=20),
        "args": ("admin",),
        "options": {"expires": 3600},
    },
    # Union: 30 11,17 * * *
    "postopus-union-11": {
        "task": "tasks.parsing_scheduler_tasks.run_all_regions_theme",
        "schedule": crontab(minute=30, hour=11),
        "args": ("union",),
        "options": {"expires": 3600},
    },
    "postopus-union-17": {
        "task": "tasks.parsing_scheduler_tasks.run_all_regions_theme",
        "schedule": crontab(minute=30, hour=17),
        "args": ("union",),
        "options": {"expires": 3600},
    },
    # Detsad: 30 13 * * *
    "postopus-detsad-13": {
        "task": "tasks.parsing_scheduler_tasks.run_all_regions_theme",
        "schedule": crontab(minute=30, hour=13),
        "args": ("detsad",),
        "options": {"expires": 3600},
    },
    # Addons (roulette): 20 6,11,18,22 * * *
    "postopus-addons-6": {
        "task": "tasks.parsing_scheduler_tasks.run_all_regions_theme",
        "schedule": crontab(minute=20, hour=6),
        "args": ("addons",),
        "options": {"expires": 3600},
    },
    "postopus-addons-11": {
        "task": "tasks.parsing_scheduler_tasks.run_all_regions_theme",
        "schedule": crontab(minute=20, hour=11),
        "args": ("addons",),
        "options": {"expires": 3600},
    },
    "postopus-addons-18": {
        "task": "tasks.parsing_scheduler_tasks.run_all_regions_theme",
        "schedule": crontab(minute=20, hour=18),
        "args": ("addons",),
        "options": {"expires": 3600},
    },
    "postopus-addons-22": {
        "task": "tasks.parsing_scheduler_tasks.run_all_regions_theme",
        "schedule": crontab(minute=20, hour=22),
        "args": ("addons",),
        "options": {"expires": 3600},
    },
    # Discovery health-recheck — еженедельно (ПН 04:00 MSK).
    # Обходит все Region.is_active=True; для каждого Community.is_active=True
    # делает wall.get + AI-категоризацию, обновляет health_status / last_post_at
    # / checked_at / suggested_category. По итогам — Telegram-alert (если есть
    # dead / dormant / changed_category). См. modules/discovery/health_check.py.
    "discovery-recheck-weekly": {
        "task": "tasks.discovery_tasks.recheck_all_active_regions",
        "schedule": crontab(hour=4, minute=0, day_of_week="mon"),
        "options": {
            "expires": 3600,
            "catchup": False,
        },
    },
    # Rolling discovery — ОТКЛЮЧЕНО 2026-06-02 (по решению владельца).
    # Алгоритмический авто-подбор кандидатов БЕЗ нейро-классификации (Groq 403)
    # даёт ~98% мусора: на Туже из 136 авто-кандидатов годных ≈0 — омонимы
    # («Тужа» ↔ фраза «не тужи(ть)»), чужие сёла (Ситки→Аляска,
    # Михайловское→Карелия), коммерция, Киров-wide паблики. Без нейронки в петле
    # подбор ведём ВРУЧНУЮ через скил `/discover_communities` (читаем посты,
    # классифицируем в чате). Вернуть, когда к discovery подключат нейро-фильтр.
    # См. tasks.discovery_tasks.discover_rolling_one_region.
    # "discovery-rolling-daily": {
    #     "task": "tasks.discovery_tasks.discover_rolling_one_region",
    #     "schedule": crontab(hour=3, minute=30),
    #     "options": {
    #         "expires": 3600,
    #         "catchup": False,
    #     },
    # },
    # Copy Setka (network repost): 7,37 * * * *
    "postopus-copy-setka-07": {
        "task": "tasks.parsing_scheduler_tasks.parse_and_publish_theme",
        "schedule": crontab(minute=7),
        "kwargs": {"region_code": "copy", "theme": "setka"},
        "options": {"expires": 1800},
    },
    "postopus-copy-setka-37": {
        "task": "tasks.parsing_scheduler_tasks.parse_and_publish_theme",
        "schedule": crontab(minute=37),
        "kwargs": {"region_code": "copy", "theme": "setka"},
        "options": {"expires": 1800},
    },
    # Поток «Кругозор» (научпоп → веером на районные паблики): раз в день 20:00 MSK
    # (вечер — пик чтения «умного»/lean-back контента). Гейт KRUGOZOR_BROADCAST_DISABLED
    # (OFF по умолчанию). Старт 1×/день; при хорошем охвате добавляется обед 13:00.
    "krugozor-broadcast-evening": {
        "task": "tasks.parsing_scheduler_tasks.parse_and_publish_theme",
        "schedule": crontab(minute=0, hour=20),
        "kwargs": {"region_code": "copy", "theme": "krugozor"},
        "options": {"expires": 3600},
    },
    # Контент-радар (Ф0.2): fan-out поллер источников каждые 10 мин круглосуточно
    # (личная лента — не публикация в сеть, ночное окно не нужно). Внутри прогона
    # поллятся только активные источники с ≥1 подпиской; пустой радар = no-op.
    "radar-poll-sources": {
        "task": "tasks.radar_tasks.poll_radar_sources",
        "schedule": crontab(minute="*/10"),
        "options": {"expires": 540, "catchup": False},
    },
    # Intake-бот «Карман» (приём форвардов каналов): каждую минуту getUpdates.
    # No-op, пока не заданы RADAR_BOT_NAME + токен (#008). Форвард поста канала
    # боту → канал в радар + подписка оператора.
    "radar-intake-bot": {
        "task": "tasks.radar_tasks.poll_radar_bot",
        "schedule": crontab(minute="*"),
        "options": {"expires": 55, "catchup": False},
    },
    # VK-интейк (приём кодов привязки VK-лички через Bots Long Poll сообщества):
    # каждую минуту. No-op, пока не задан RADAR_VK_COMMUNITY_ID + community-токен
    # (#008). Юзер пишет код сообществу-точке → ловим vk_id → vk_dm-вывод в личку.
    "radar-vk-intake": {
        "task": "tasks.radar_tasks.poll_radar_vk_intake",
        "schedule": crontab(minute="*"),
        "options": {"expires": 55, "catchup": False},
    },
    # Ретенция ленты радара: элементы старше 30 дней (RADAR_ITEMS_RETENTION_DAYS)
    # удаляются ночью в 03:20 (после cleanup-daily в 03:00). Сохранёнки —
    # снимки, не страдают (FK SET NULL).
    "radar-items-retention-daily": {
        "task": "tasks.radar_tasks.cleanup_old_radar_items",
        "schedule": crontab(minute=20, hour=3),
        "options": {"expires": 3600, "catchup": False},
    },
    # Watchdog поллера радара (#018): раз в час на :12. Алёртит только если есть
    # активные подписанные источники, а heartbeat протух (>40 мин) — retired≠dead.
    "radar-poll-watchdog": {
        "task": "tasks.radar_tasks.check_radar_poll_heartbeat",
        "schedule": crontab(minute=12),
        "options": {"expires": 1800, "catchup": False},
    },
    # Flow B: зеркало стены ВК-сообщества «Гоньба» → Telegram @gonba_life.
    # Каждые ~20 мин в активные часы; cap постов/run в самой задаче (анти-флуд).
    "telegram-gonba-mirror": {
        "task": "tasks.parsing_scheduler_tasks.mirror_community_to_telegram",
        "schedule": crontab(minute="10,40", hour="7-23"),
        "options": {"expires": 1200, "catchup": False},
    },
    # Сетевая рассылка (директива brain 2026-06-14): диспетчер-публикатор раз в
    # минуту публикует wall.post немедленно в назревшие кампании (НЕ в VK-отложку).
    # No-op, пока нет запланированных кампаний или BROADCAST_DISABLED.
    "broadcast-dispatch": {
        "task": "tasks.broadcast_tasks.dispatch_broadcasts",
        "schedule": crontab(minute="*"),
        "options": {"expires": 55, "catchup": False},
    },
    # Watchdog рассылки (#018): раз в час на :22. Алёртит только если есть
    # просроченные кампании, а диспетчер их не разослал (молча встал).
    "broadcast-watchdog": {
        "task": "tasks.broadcast_tasks.check_broadcast_heartbeat",
        "schedule": crontab(minute=22),
        "options": {"expires": 1800, "catchup": False},
    },
}


if __name__ == "__main__":
    # Для отладки
    logger.info("Celery app configured successfully!")
    logger.info(f"Broker: {app.conf.broker_url}")
    logger.info(f"Backend: {app.conf.result_backend}")
    logger.info(f"Timezone: {app.conf.timezone}")
    logger.info(f"Beat schedule: {list(app.conf.beat_schedule.keys())}")
