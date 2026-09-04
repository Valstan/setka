"""Диспетчер планировщика предложки: репосты в дублёры + оригиналы в режиме queue.

Беат раз в минуту (24/7 — владелец назначает любое время) берёт строки
``ad_scheduled_posts`` с ``kind in ('suggested','repost')``, ``status='scheduled'``
и наступившим ``next_attempt_at``:

* ``kind='suggested'`` (режим ``queue``): публикует предложенный пост «сейчас»
  (``publish_suggested`` без publish_date) и фиксирует выход через
  ``publish_reconciler.record_published``;
* ``kind='repost'``: ждёт, пока оригинал реально вышел (``src.status='published'``
  либо ``is_published`` по ``wall.getById``; при этом фиксирует оригинал сам, не
  дожидаясь X:45), затем ``wall.repost`` от имени сообщества-дублёра и
  ``record_published`` для строки-репоста → AdPublication + AdPayment(awaiting).

Гарантии (образец — ``modules/broadcast/dispatcher.py``):
- **lease-claim**: guarded UPDATE ``next_attempt_at = now + LEASE`` ∧
  ``attempts+1`` ∧ ``WHERE status='scheduled' AND next_attempt_at <= now`` —
  rowcount==1 значит строку взяли мы; конкурент увидит сдвинутый срок. Умерший
  процесс зависших строк не оставляет: lease истекает и строка снова due;
- **throttle ≥5 с** между реальными VK-записями (анти-Captcha);
- **per-row изоляция**: ошибка одной строки не валит остальные;
- **коды VK** через ``modules/promotion/vk_errors``: 9/14 → все due-строки
  сдвигаются на cooldown, алёрт, тик прерван; 214/220 → строка ``failed``;
  219 → ``failed`` + алёрт; без кода → ретрай через 5 мин, не более
  ``MAX_ATTEMPTS``;
- **дедлайн**: оригинал не вышел за ``REPOST_DEADLINE`` после ``publish_date`` →
  репост ``failed`` + алёрт.

Время: ``publish_date``/``next_attempt_at`` — МСК wall-clock naive; сравниваем
с ``_now_msk()``. Heartbeat в Redis + watchdog раз в час.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy import update as sa_update

from database.models import AdScheduledPost
from modules.ad_cabinet.interaction_log import log_interaction
from modules.ad_cabinet.publish_reconciler import record_published

logger = logging.getLogger(__name__)

MSK = timezone(timedelta(hours=3))
DISPATCH_KINDS = ("suggested", "repost")

LEASE = timedelta(minutes=2)  # lease при claim; повтор «оригинал ещё не вышел»
RETRY_WAIT = timedelta(minutes=5)  # ретрай после ошибки без кода
REPOST_DEADLINE = timedelta(hours=2)  # сколько ждём выхода оригинала
MAX_ATTEMPTS = 6

HEARTBEAT_KEY = "setka:ad_repost_last_dispatch"
_HEARTBEAT_TTL = 14 * 24 * 3600
COOLDOWN_KEY = "setka:ad_repost_stale_cooldown"
ALERT_COOLDOWN_SECONDS = 6 * 3600
OVERDUE_GRACE_SECONDS = 15 * 60


def _now_msk() -> datetime:
    return datetime.now(MSK).replace(tzinfo=None)


def _redis():
    from modules.bulletin_heartbeat import _redis as _r

    return _r()


def touch_heartbeat(*, ts: Optional[float] = None) -> None:
    """Heartbeat «диспетчер живой» (best-effort)."""
    try:
        client = _redis()
        if client is None:
            return
        client.setex(HEARTBEAT_KEY, _HEARTBEAT_TTL, str(int(ts if ts is not None else time.time())))
    except Exception:  # pragma: no cover - наблюдаемость не валит публикацию
        logger.warning("ad_repost heartbeat write failed", exc_info=True)


async def _maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value


def _default_alert(text: str) -> None:  # pragma: no cover - сеть
    from modules.ad_cabinet import owner_ping

    try:
        owner_ping.notify_owner(text)
    except Exception:
        logger.warning("ad_repost alert failed", exc_info=True)


async def _claim(session, row_id: int, now: datetime) -> bool:
    """Атомарно взять строку в работу (lease). True — мы первыми."""
    res = await session.execute(
        sa_update(AdScheduledPost)
        .where(
            AdScheduledPost.id == row_id,
            AdScheduledPost.status == "scheduled",
            AdScheduledPost.next_attempt_at <= now,
        )
        .values(next_attempt_at=now + LEASE, attempts=AdScheduledPost.attempts + 1)
    )
    return bool(res.rowcount or 0)


async def _shift_all_due(session, now: datetime, cooldown: timedelta) -> int:
    """9/14: все due-строки планировщика — на cooldown вперёд (модуль замолкает)."""
    res = await session.execute(
        sa_update(AdScheduledPost)
        .where(
            AdScheduledPost.kind.in_(DISPATCH_KINDS),
            AdScheduledPost.status == "scheduled",
            AdScheduledPost.next_attempt_at <= now + LEASE,
        )
        .values(next_attempt_at=now + cooldown)
    )
    return int(res.rowcount or 0)


async def _apply_error(
    session, row: AdScheduledPost, res: Dict[str, Any], now: datetime, alert
) -> str:
    """Реакция на отказ VK. Возвращает ``'stop'`` (9/14), ``'failed'`` или ``'retry'``."""
    from modules.promotion.vk_errors import classify_promo_error, extract_vk_error_code

    message = str(res.get("error") or "VK не принял")
    code = res.get("vk_error_code") or extract_vk_error_code(message)
    action = classify_promo_error(code, message)
    if action.kind == "stop_module":
        cooldown = timedelta(seconds=action.module_cooldown_seconds or 3600)
        shifted = await _shift_all_due(session, now, cooldown)
        row.next_attempt_at = now + cooldown
        row.error_message = message[:500]
        await _maybe_await(
            alert(
                f"⛔ Планировщик предложки: {action.reason}. "
                f"Репосты остановлены до {(now + cooldown):%d.%m %H:%M} МСК "
                f"({shifted} строк)."
            )
        )
        return "stop"
    if action.kind == "blacklist_donor":
        row.status = "failed"
        row.error_message = message[:500]
        if code == 219:
            await _maybe_await(
                alert(
                    f"⚠️ Репост {row.id} в {row.community_vk_id}: VK счёл пост рекламой (219). "
                    "Договоритесь с владельцем стены вручную."
                )
            )
        return "failed"
    if int(row.attempts or 0) >= MAX_ATTEMPTS:
        row.status = "failed"
        row.error_message = f"{MAX_ATTEMPTS} попыток: {message}"[:500]
        return "failed"
    row.next_attempt_at = now + RETRY_WAIT
    row.error_message = message[:500]
    return "retry"


async def _default_publisher_factory(session):
    async def factory(gid: int):
        from modules.publisher.vk_publisher_extended import VKPublisher

        return await VKPublisher.create_with_policy(session, target_group_id=int(gid))

    return factory


async def run_repost_dispatch(
    *,
    session_factory: Optional[Callable] = None,
    publisher_factory: Optional[Callable[[int], Awaitable[Any]]] = None,
    is_published: Optional[Callable[[int, int], Optional[bool]]] = None,
    interval: Optional[float] = None,
    now: Optional[datetime] = None,
    alert: Optional[Callable[[str], Any]] = None,
) -> Dict[str, Any]:
    """Один тик диспетчера. Возвращает счётчики."""
    from config.runtime import ad_repost_disabled, get_broadcast_post_interval_seconds

    if ad_repost_disabled():
        return {"skipped": "disabled", "taken": 0, "published": 0}
    if session_factory is None:
        from database.connection import AsyncSessionLocal

        session_factory = AsyncSessionLocal
    now = now or _now_msk()
    interval = get_broadcast_post_interval_seconds() if interval is None else float(interval)
    alert = alert or _default_alert

    stats = {"taken": 0, "published": 0, "waiting": 0, "failed": 0, "retry": 0, "stopped": False}
    async with session_factory() as session:
        if publisher_factory is None:
            publisher_factory = await _default_publisher_factory(session)
        due_ids = (
            (
                await session.execute(
                    select(AdScheduledPost.id).where(
                        AdScheduledPost.kind.in_(DISPATCH_KINDS),
                        AdScheduledPost.status == "scheduled",
                        AdScheduledPost.next_attempt_at.isnot(None),
                        AdScheduledPost.next_attempt_at <= now,
                    )
                    # Оригиналы (queue-режим) раньше репостов: репост в том же
                    # тике уже увидит вышедший оригинал.
                    .order_by(
                        AdScheduledPost.kind.desc(),
                        AdScheduledPost.publish_date.asc(),
                        AdScheduledPost.id.asc(),
                    )
                )
            )
            .scalars()
            .all()
        )
        if not due_ids:
            touch_heartbeat()
            return stats

        # VK-проверку выхода собираем лениво и один раз — только когда какой-то
        # репост реально ждёт оригинал (иначе в тестах и при оригиналах со
        # статусом published токены не нужны вовсе).
        checker_cache: Dict[str, Any] = {"fn": is_published, "built": is_published is not None}

        async def get_checker():
            if not checker_cache["built"]:
                from modules.ad_cabinet.publish_reconciler import build_default_checker_from_routing

                checker_cache["fn"] = await build_default_checker_from_routing()
                checker_cache["built"] = True
            return checker_cache["fn"]

        posted_any = False
        for row_id in due_ids:
            if not await _claim(session, int(row_id), now):
                await session.commit()
                continue
            await session.commit()
            row = await session.get(AdScheduledPost, int(row_id))
            if row is None:
                continue
            stats["taken"] += 1
            try:
                outcome = await _handle_row(
                    session,
                    row,
                    now=now,
                    publisher_factory=publisher_factory,
                    get_checker=get_checker,
                    alert=alert,
                    throttle=(interval if posted_any else 0.0),
                )
            except Exception as e:  # noqa: BLE001 — per-row изоляция
                logger.warning("ad_repost: row %s failed: %s", row_id, e, exc_info=True)
                row.next_attempt_at = now + RETRY_WAIT
                row.error_message = str(e)[:500]
                outcome = "retry"
            await session.commit()
            if outcome in ("published",):
                posted_any = True
                stats["published"] += 1
            elif outcome == "waiting":
                stats["waiting"] += 1
            elif outcome == "failed":
                stats["failed"] += 1
            elif outcome == "retry":
                stats["retry"] += 1
                posted_any = True  # VK-вызов был — троттлим следующий
            elif outcome == "stop":
                stats["stopped"] = True
                break
        touch_heartbeat()
    return stats


async def _handle_row(
    session,
    row: AdScheduledPost,
    *,
    now: datetime,
    publisher_factory,
    get_checker,
    alert,
    throttle: float,
) -> str:
    """Обработать одну заклеймленную строку. Возвращает исход."""
    if row.kind == "suggested":
        if throttle > 0:
            await asyncio.sleep(throttle)
        publisher = await publisher_factory(int(row.community_vk_id))
        res = await publisher.publish_suggested(
            int(row.community_vk_id),
            int(row.vk_postponed_post_id),
            signed=bool(row.signed),
            publish_date=None,
        )
        if res.get("success"):
            await record_published(session, row, vk_post_id=res.get("post_id"))
            return "published"
        return await _apply_error(session, row, res, now, alert)

    # kind == 'repost'
    src = (
        await session.get(AdScheduledPost, int(row.source_post_id)) if row.source_post_id else None
    )
    if src is None or src.status in ("failed", "cancelled", "rejected"):
        row.status = "failed"
        row.error_message = "оригинал не опубликован (отменён/отклонён/ошибка)"
        return "failed"

    if src.status != "published":
        state = None
        is_published = await get_checker() if src.vk_postponed_post_id else None
        if is_published is not None:
            try:
                state = is_published(int(src.community_vk_id), int(src.vk_postponed_post_id))
            except Exception as e:  # pragma: no cover - защита
                logger.warning("ad_repost: is_published failed for %s: %s", src.id, e)
                state = None
        if state is True:
            # Оригинал вышел — фиксируем сами, не ждём реконсилер X:45.
            await record_published(session, src)
        else:
            deadline = (src.publish_date or now) + REPOST_DEADLINE
            if now < deadline:
                row.next_attempt_at = now + LEASE
                return "waiting"
            row.status = "failed"
            row.error_message = "оригинал не вышел за 2 ч после назначенного времени"
            await _maybe_await(
                alert(
                    f"⚠️ Репост {row.id} в {row.community_vk_id} отменён: оригинал "
                    f"{src.community_vk_id}_{src.vk_postponed_post_id} не вышел за 2 ч."
                )
            )
            return "failed"

    if throttle > 0:
        await asyncio.sleep(throttle)
    publisher = await publisher_factory(int(row.community_vk_id))
    res = await publisher.publish_repost(
        group_id=int(row.community_vk_id),
        source_owner_id=int(src.community_vk_id),
        source_post_id=int(src.vk_postponed_post_id),
    )
    if res.get("success"):
        await record_published(session, row, vk_post_id=res.get("post_id"))
        return "published"
    outcome = await _apply_error(session, row, res, now, alert)
    if outcome == "failed":
        log_interaction(
            session,
            kind="moderation_failed",
            client_id=row.client_id,
            scheduled_post_id=row.id,
            summary=f"Репост в {row.community_vk_id} не удался: {row.error_message}",
            actor="system",
        )
    return outcome


async def has_overdue_rows(now: Optional[datetime] = None, *, session_factory=None) -> bool:
    """Есть ли строки планировщика, которые диспетчер должен был взять давно."""
    now = now or _now_msk()
    if session_factory is None:
        from database.connection import AsyncSessionLocal

        session_factory = AsyncSessionLocal
    cutoff = now - timedelta(seconds=OVERDUE_GRACE_SECONDS)
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(AdScheduledPost.id).where(
                    AdScheduledPost.kind.in_(DISPATCH_KINDS),
                    AdScheduledPost.status == "scheduled",
                    AdScheduledPost.next_attempt_at.isnot(None),
                    AdScheduledPost.next_attempt_at < cutoff,
                )
            )
        ).all()
    return len(rows) > 0


async def maybe_alert_stale(
    *,
    telegram_token: Optional[str] = None,
    chat_id: Optional[str] = None,
    now: Optional[datetime] = None,
    session_factory=None,
) -> str:
    """Watchdog: алёрт, если есть просроченные строки (диспетчер молча встал)."""
    now = now or _now_msk()
    if not await has_overdue_rows(now, session_factory=session_factory):
        return "no-overdue"
    if not telegram_token or not chat_id:
        return "skipped:no-telegram-config"
    client = _redis()
    try:
        if client is not None and client.get(COOLDOWN_KEY):
            return "skipped:cooldown"
    except Exception:  # pragma: no cover
        pass
    last: Optional[int] = None
    try:
        if client is not None:
            v = client.get(HEARTBEAT_KEY)
            last = int(v) if v else None
    except Exception:  # pragma: no cover
        last = None
    hb = f"{(time.time() - last) / 3600:.1f} ч назад" if last else "никогда"
    message = (
        "⚠️ <b>SETKA: планировщик предложки встал</b>\n\n"
        "Есть репосты/оригиналы, чьё время давно прошло, а диспетчер их не взял.\n"
        f"Последний тик: <b>{hb}</b>.\n\n"
        "Проверь: <code>systemctl status setka-celery-beat setka-celery-worker</code>."
    )
    try:
        from modules import telegram_http as tg_http

        resp = tg_http.post(
            f"https://api.telegram.org/bot{telegram_token}/sendMessage",
            json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"},
        )
        if resp.status_code != 200:
            return "error:http-" + str(resp.status_code)
        if client is not None:
            client.setex(COOLDOWN_KEY, ALERT_COOLDOWN_SECONDS, "1")
        return "alert-sent"
    except Exception as exc:  # pragma: no cover - сеть
        logger.error("ad_repost stale alert failed: %s", exc)
        return "error:" + str(exc)


__all__: List[str] = [
    "run_repost_dispatch",
    "maybe_alert_stale",
    "has_overdue_rows",
    "touch_heartbeat",
    "LEASE",
    "RETRY_WAIT",
    "REPOST_DEADLINE",
    "MAX_ATTEMPTS",
]
