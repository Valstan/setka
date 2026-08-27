"""Диспетчер раскрутки: подобрать пары, записать план, при боевом режиме — опубликовать.

Калька движка сетевой рассылки (``modules/broadcast/dispatcher.py``) — те же
гарантии, потому что задача та же: автономная публикация под конкурентным беатом.

- **Идемпотентность.** Строка ``promo_actions`` клеймится через
  ``INSERT … ON CONFLICT DO NOTHING`` и коммитится **до** обращения к ВК. Два
  уникальных индекса — по донору и по цели за слот — и есть настоящее соблюдение
  квот «одно промо в неделю на донора» и «одно на цель»: они держатся при гонке
  беата, при рестарте на середине прогона и при упавшем Redis.
- **Реклейм зависших.** ``pending`` старше десяти минут = процесс умер между
  claim и записью результата. Помечаем ``error`` и **не перепубликовываем**:
  статус поста в ВК неизвестен, а дубль на чужой стене хуже пропуска.
- **Throttle.** Пять секунд между реальными публикациями — замеренная граница
  (16 постов подряд по 5 с без капчи, бурст по 3 с капчу ловит).
- **Сухой прогон.** Канал в ``dry_run`` проходит весь путь, включая сборку текста,
  и записывает его в ``body`` со статусом ``dry_run``, ничего не отправляя. Это
  не заглушка, а рабочий режим этапа 1: владелец неделю читает в разделе ровно
  тот текст, который ушёл бы на стену.

**Что диспетчер не делает.** Не пишет на чужие стены: донор — всегда сообщество
сети, а публикация идёт ключом самого донора. Не рассылает сообщений и не
приглашает. Кандидатов для обращения к чужим админам модуль только готовит.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy import update as sa_update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from config.promo import get_max_actions_per_day, get_oblast_group_id, promo_disabled
from database.models import (
    PromoAction,
    PromoDonorBlacklist,
    PromoEnrollment,
    PromoSettings,
    Region,
    RegionMemberSnapshot,
    VKToken,
)
from modules.promotion import copy as promo_copy
from modules.promotion.pairing import DonorCandidate, TargetCandidate, plan_pairs
from modules.promotion.settings import module_paused, resolve_channel
from modules.region_links import build_neighbor_graph, community_url, short_name

logger = logging.getLogger(__name__)

MSK = timezone(timedelta(hours=3))

HEARTBEAT_KEY = "setka:promo_last_dispatch"
_HEARTBEAT_TTL = 14 * 24 * 3600
COOLDOWN_KEY = "setka:promo_stale_alert_cooldown"
ALERT_COOLDOWN_SECONDS = 6 * 3600

# 'pending' старше этого = процесс умер между claim и записью результата.
STALE_PENDING_SECONDS = 10 * 60

# Сколько суток без успешного действия считать простоем. Раскрутка работает
# редко (одно промо в неделю на донора), поэтому порог не часы, как у сводок,
# а трое суток — иначе watchdog будет орать на штатную тишину.
STALE_DAYS = 3


def _now_msk() -> datetime:
    return datetime.now(MSK).replace(tzinfo=None)


def _redis():
    from modules.bulletin_heartbeat import _redis as _r

    return _r()


def touch_heartbeat(*, ts: Optional[float] = None) -> None:
    """Отметить «диспетчер тикнул» (best-effort, не роняет прогон)."""
    try:
        client = _redis()
        if client is None:
            return
        client.setex(HEARTBEAT_KEY, _HEARTBEAT_TTL, str(int(ts if ts is not None else time.time())))
    except Exception:  # pragma: no cover - наблюдаемость не валит работу
        logger.warning("promo heartbeat write failed", exc_info=True)


def slot_key_week(now: Optional[datetime] = None) -> str:
    """Ключ недельного слота: ISO-неделя, ``2026-W35``.

    ISO, а не «номер недели года»: 31 декабря и 1 января должны попасть в один
    слот, если это одна неделя, иначе донор получит два промо подряд на стыке лет.
    """
    return (now or _now_msk()).strftime("%G-W%V")


def in_quiet_hours(now: datetime, start: int, end: int) -> bool:
    """Тихие часы: с ``start`` вечера до ``end`` утра.

    Окно проходит через полночь (19 → 10), поэтому сравнение с ИЛИ, а не с И.
    Ночной постинг — сигнал «бот» и для читателя, и для антиспама.
    """
    hour = now.hour
    if start == end:
        return False
    if start > end:
        return hour >= start or hour < end
    return start <= hour < end


async def _claim(
    session,
    *,
    channel: str,
    donor_group_id: Optional[int],
    donor_region_id: Optional[int],
    target_region_id: int,
    hop: int,
    slot_key: str,
    dry_run: bool,
) -> bool:
    """Атомарно заклеймить действие. ``True`` — мы первыми.

    ``on_conflict_do_nothing()`` без указания индекса — намеренно: конфликт может
    прийти по любому из двух уникумов (донор занят в этом слоте либо цель уже
    получила своё), и оба означают одно — действие не наше.
    """
    stmt = (
        pg_insert(PromoAction)
        .values(
            channel=channel,
            donor_group_id=donor_group_id,
            donor_region_id=donor_region_id,
            target_region_id=target_region_id,
            hop=hop,
            slot_key=slot_key,
            status="pending",
            dry_run=dry_run,
            planned_at=datetime.utcnow(),
        )
        .on_conflict_do_nothing()
    )
    result = await session.execute(stmt)
    return (result.rowcount or 0) == 1


async def _finish(
    session,
    *,
    channel: str,
    target_region_id: int,
    slot_key: str,
    values: Dict[str, Any],
) -> None:
    """Записать исход в заклеймленную строку."""
    await session.execute(
        sa_update(PromoAction)
        .where(
            PromoAction.channel == channel,
            PromoAction.target_region_id == target_region_id,
            PromoAction.slot_key == slot_key,
        )
        .values(**values)
    )


async def reclaim_stale(session, *, now: Optional[datetime] = None) -> int:
    """Закрыть зависшие ``pending``. Возвращает число реклеймленных.

    Не перепубликовываем: между claim и падением пост мог уйти, и повтор дал бы
    дубль на живой стене. Оператор видит строку как ошибку и решает сам.
    """
    cutoff = (now or datetime.utcnow()) - timedelta(seconds=STALE_PENDING_SECONDS)
    result = await session.execute(
        sa_update(PromoAction)
        .where(PromoAction.status == "pending", PromoAction.planned_at < cutoff)
        .values(
            status="error",
            error="зависло в pending (рестарт?) — статус публикации неизвестен",
        )
    )
    return int(result.rowcount or 0)


async def _actions_today(session, *, now: Optional[datetime] = None) -> int:
    """Сколько действий уже запланировано за сегодня — суточный потолок.

    Считается по БД, а не по Redis: Redis-квоты у нас fail-open, и при его
    недоступности потолок просто исчез бы. Сухие прогоны считаются наравне с
    боевыми — иначе репетиция не показывала бы реальный темп.
    """
    day_start = (now or _now_msk()).replace(hour=0, minute=0, second=0, microsecond=0)
    count = (
        await session.execute(
            select(func.count(PromoAction.id)).where(PromoAction.planned_at >= day_start)
        )
    ).scalar()
    return int(count or 0)


async def _busy_in_slot(session, *, channel: str, slot_key: str):
    """Кто уже отработал в этом слоте: ``(группы доноров, регионы целей)``."""
    rows = await session.execute(
        select(PromoAction.donor_group_id, PromoAction.target_region_id).where(
            PromoAction.channel == channel, PromoAction.slot_key == slot_key
        )
    )
    donors: List[int] = []
    targets: List[int] = []
    for donor_group_id, target_region_id in rows.all():
        if donor_group_id is not None:
            donors.append(int(donor_group_id))
        if target_region_id is not None:
            targets.append(int(target_region_id))
    return donors, targets


async def _blacklisted_donors(session, *, now: Optional[datetime] = None) -> set:
    """Группы, которым сейчас нельзя отдавать промо."""
    now = now or datetime.utcnow()
    rows = await session.execute(
        select(PromoDonorBlacklist.donor_group_id, PromoDonorBlacklist.until)
    )
    blocked = set()
    for group_id, until in rows.all():
        if until is None or until > now:
            blocked.add(int(group_id))
    return blocked


async def _latest_members(session) -> Dict[int, int]:
    latest = (
        select(
            RegionMemberSnapshot.region_id.label("region_id"),
            func.max(RegionMemberSnapshot.snapshot_date).label("day"),
        )
        .group_by(RegionMemberSnapshot.region_id)
        .subquery()
    )
    rows = await session.execute(
        select(RegionMemberSnapshot.region_id, RegionMemberSnapshot.members_count).join(
            latest,
            (RegionMemberSnapshot.region_id == latest.c.region_id)
            & (RegionMemberSnapshot.snapshot_date == latest.c.day),
        )
    )
    return {region_id: members for region_id, members in rows.all()}


async def _community_token_groups(session) -> set:
    rows = await session.execute(
        select(VKToken.community_id).where(
            VKToken.community_id.isnot(None), VKToken.is_active.is_(True)
        )
    )
    return {abs(int(cid)) for (cid,) in rows.all() if cid is not None}


def _region_url(region: Region) -> str:
    screen_name = None
    config = getattr(region, "config", None)
    if isinstance(config, dict):
        value = config.get("screen_name")
        if isinstance(value, str) and value:
            screen_name = value
    return community_url(region.vk_group_id, screen_name)


async def _default_publish(session):
    """Боевой публикатор: ``wall.post`` каскадом community → МАМА → VALSTAN.

    Именно через ``create_with_policy`` с указанием группы-донора: тогда первым
    берётся ключ самого донора, и промо не расходует user-аккаунт, которым
    публикуется вся сеть.
    """
    from modules.publisher.vk_publisher_extended import VKPublisher

    async def _publish(group_id: int, text: str) -> Dict[str, Any]:
        publisher = await VKPublisher.create_with_policy(session, target_group_id=group_id)
        return await publisher.publish_bulletin(group_id, text, attachments=None)

    return _publish


async def _maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value


async def run_promo_dispatch(
    session,
    *,
    publish: Optional[Callable] = None,
    now: Optional[datetime] = None,
    interval: Optional[float] = None,
) -> Dict[str, Any]:
    """Один тик диспетчера. Возвращает сводку, никогда не бросает наружу."""
    from config.promo import get_post_interval_seconds

    now = now or _now_msk()
    touch_heartbeat()

    settings = (await session.execute(select(PromoSettings).limit(1))).scalar_one_or_none()
    if settings is None:
        return {"status": "skipped:no-settings", "planned": 0, "published": 0}

    disabled = promo_disabled()
    paused = module_paused(settings.paused_until, now=datetime.utcnow())

    reclaimed = await reclaim_stale(session, now=datetime.utcnow())
    if reclaimed:
        await session.commit()
        logger.warning("promo dispatch: реклеймлено зависших действий: %s", reclaimed)

    if in_quiet_hours(now, int(settings.quiet_hours_start), int(settings.quiet_hours_end)):
        return {
            "status": "skipped:quiet-hours",
            "planned": 0,
            "published": 0,
            "reclaimed": reclaimed,
        }

    cap = min(int(settings.max_actions_per_day or 0), get_max_actions_per_day())
    spent = await _actions_today(session, now=now)
    budget = max(0, cap - spent)
    if budget <= 0:
        return {
            "status": "skipped:daily-cap",
            "planned": 0,
            "published": 0,
            "cap": cap,
            "spent": spent,
            "reclaimed": reclaimed,
        }

    slot_key = slot_key_week(now)
    channel = resolve_channel(
        "promo_post", settings.channels, module_disabled=disabled, paused=paused
    )
    digest_channel = resolve_channel(
        "oblast_digest", settings.channels, module_disabled=disabled, paused=paused
    )

    regions = (await session.execute(select(Region))).scalars().all()
    by_id = {r.id: r for r in regions}
    members = await _latest_members(session)
    token_groups = await _community_token_groups(session)
    blacklist = await _blacklisted_donors(session)

    enrolled = {
        row.region_id
        for row in (
            await session.execute(select(PromoEnrollment).where(PromoEnrollment.status == "active"))
        )
        .scalars()
        .all()
    }

    targets: List[TargetCandidate] = []
    donors: List[DonorCandidate] = []
    for region in regions:
        if region.kind != "raion" or not region.is_active or not region.vk_group_id:
            continue
        count = members.get(region.id)
        group_id = int(region.vk_group_id)
        if region.id in enrolled:
            targets.append(
                TargetCandidate(
                    region_id=region.id,
                    code=region.code,
                    group_id=group_id,
                    members=count,
                )
            )
        if (
            count is not None
            and count >= int(settings.donor_min_members)
            and abs(group_id) not in blacklist
        ):
            donors.append(
                DonorCandidate(
                    region_id=region.id,
                    code=region.code,
                    group_id=group_id,
                    members=count,
                    has_community_token=abs(group_id) in token_groups,
                )
            )

    busy_donors, busy_targets = await _busy_in_slot(
        session, channel="promo_post", slot_key=slot_key
    )
    pairs, orphans = plan_pairs(
        targets,
        donors,
        build_neighbor_graph(regions),
        second_hop_enabled=bool(settings.second_hop_enabled),
        max_pairs=budget,
        busy_donor_group_ids=busy_donors,
        busy_target_region_ids=busy_targets,
    )

    if publish is None:
        publish = await _default_publish(session)
    if interval is None:
        interval = get_post_interval_seconds()

    planned = 0
    published = 0
    errors = 0
    first_real = True

    if channel.enabled:
        for pair in pairs:
            claimed = await _claim(
                session,
                channel="promo_post",
                donor_group_id=pair.donor.group_id,
                donor_region_id=pair.donor.region_id,
                target_region_id=pair.target.region_id,
                hop=pair.hop,
                slot_key=slot_key,
                dry_run=channel.dry_run,
            )
            if not claimed:
                continue
            await session.commit()
            planned += 1

            target_region = by_id[pair.target.region_id]
            body = promo_copy.render_promo_post(
                target_name=short_name(target_region.name, target_region.center_city),
                target_url=_region_url(target_region),
                hop=pair.hop,
            )

            if channel.dry_run:
                await _finish(
                    session,
                    channel="promo_post",
                    target_region_id=pair.target.region_id,
                    slot_key=slot_key,
                    values={"status": "dry_run", "body": body, "vk_method": "wall.post"},
                )
                await session.commit()
                continue

            if not first_real and interval:
                await asyncio.sleep(interval)
            first_real = False

            result = await _publish_one(
                session,
                publish=publish,
                channel="promo_post",
                group_id=pair.donor.group_id,
                target_region_id=pair.target.region_id,
                slot_key=slot_key,
                body=body,
            )
            if result:
                published += 1
            else:
                errors += 1

    digest = None
    if digest_channel.enabled and orphans and settings.oblast_fallback_enabled:
        digest = await _dispatch_oblast_digest(
            session,
            orphans=orphans,
            by_id=by_id,
            slot_key=slot_key,
            state=digest_channel,
            publish=publish,
            settings=settings,
        )
        if digest and digest.get("planned"):
            planned += 1
            published += int(digest.get("published") or 0)

    result = {
        "status": "ok",
        "slot": slot_key,
        "mode": "dry-run" if channel.dry_run else "live",
        "reason": channel.reason,
        "planned": planned,
        "published": published,
        "errors": errors,
        "orphans": len(orphans),
        "reclaimed": reclaimed,
        "budget": budget,
    }
    if digest:
        result["digest"] = digest
    if planned or errors:
        logger.info("promo dispatch: %s", result)
    return result


async def _publish_one(
    session,
    *,
    publish: Callable,
    channel: str,
    group_id: int,
    target_region_id: int,
    slot_key: str,
    body: str,
) -> bool:
    """Опубликовать одно действие и записать исход. Ошибка не валит остальные."""
    from modules.promotion.vk_errors import classify_promo_error

    try:
        res = await _maybe_await(publish(group_id, body)) or {}
    except Exception as exc:  # noqa: BLE001 - per-target изоляция
        res = {"success": False, "error": str(exc)}

    if res.get("success"):
        await _finish(
            session,
            channel=channel,
            target_region_id=target_region_id,
            slot_key=slot_key,
            values={
                "status": "published",
                "body": body,
                "vk_method": "wall.post",
                "vk_post_id": res.get("post_id"),
                "post_url": res.get("url"),
                "token_name": (res.get("via") or "").split(":")[-1] or None,
                "api_calls": 1,
                "published_at": datetime.utcnow(),
            },
        )
        await session.commit()
        return True

    message = str(res.get("error") or "publish failed")
    action = classify_promo_error(None, message)
    await _finish(
        session,
        channel=channel,
        target_region_id=target_region_id,
        slot_key=slot_key,
        values={
            "status": "error",
            "body": body,
            "vk_method": "wall.post",
            "error": message[:500],
            "vk_error_code": _code_of(message),
            "api_calls": 1,
            "published_at": datetime.utcnow(),
        },
    )
    await session.commit()
    await apply_error_action(session, action=action, donor_group_id=group_id)
    return False


def _code_of(message: str) -> Optional[int]:
    from modules.promotion.vk_errors import extract_vk_error_code

    return extract_vk_error_code(message)


async def apply_error_action(session, *, action, donor_group_id: Optional[int]) -> None:
    """Исполнить решение по ошибке ВК: пауза модуля либо чёрный список донора.

    Пауза пишется в БД, а не в Redis: перезапуск кэша не должен снимать запрет,
    выданный самим ВК.
    """
    now = datetime.utcnow()
    if action.kind == "stop_module" and action.module_cooldown_seconds:
        await session.execute(
            sa_update(PromoSettings)
            .where(PromoSettings.id == 1)
            .values(
                paused_until=now + timedelta(seconds=action.module_cooldown_seconds),
                paused_reason=action.reason,
            )
        )
        await session.commit()
        logger.error("promo: модуль остановлен — %s", action.reason)
        return

    if action.kind == "blacklist_donor" and donor_group_id is not None:
        until = now + timedelta(hours=action.blacklist_hours) if action.blacklist_hours else None
        await session.execute(
            pg_insert(PromoDonorBlacklist)
            .values(donor_group_id=abs(int(donor_group_id)), reason=action.reason, until=until)
            .on_conflict_do_update(
                index_elements=["donor_group_id"],
                set_={"reason": action.reason, "until": until},
            )
        )
        await session.commit()
        logger.warning("promo: донор %s в чёрном списке — %s", donor_group_id, action.reason)


async def _dispatch_oblast_digest(
    session,
    *,
    orphans,
    by_id: Dict[int, Region],
    slot_key: str,
    state,
    publish: Callable,
    settings: PromoSettings,
) -> Optional[Dict[str, Any]]:
    """Один пост областной ленты со списком районов без сетевого донора.

    Одна строка журнала на весь дайджест, а не по строке на район: иначе уникум
    «один донор в неделю» разрешил бы только первый район, и очередь из
    одиннадцати недосягаемых растянулась бы на одиннадцать недель. Кого именно
    представили — видно в тексте действия.
    """
    oblast_group_id = int(settings.oblast_group_id or get_oblast_group_id())
    oblast_region = next(
        (r for r in by_id.values() if r.vk_group_id and int(r.vk_group_id) == oblast_group_id),
        None,
    )
    if oblast_region is None:
        logger.warning("promo digest: областной регион для группы %s не найден", oblast_group_id)
        return None

    picked = []
    for orphan in orphans[:5]:
        region = by_id.get(orphan.target.region_id)
        if region is None:
            continue
        picked.append(
            {"name": short_name(region.name, region.center_city), "url": _region_url(region)}
        )
    if not picked:
        return None

    claimed = await _claim(
        session,
        channel="oblast_digest",
        donor_group_id=oblast_group_id,
        donor_region_id=oblast_region.id,
        target_region_id=oblast_region.id,
        hop=3,
        slot_key=slot_key,
        dry_run=state.dry_run,
    )
    if not claimed:
        return None
    await session.commit()

    from config.promo import get_network_list_url

    body = promo_copy.render_oblast_digest(picked, site_url=get_network_list_url())
    if not body:
        return None

    if state.dry_run:
        await _finish(
            session,
            channel="oblast_digest",
            target_region_id=oblast_region.id,
            slot_key=slot_key,
            values={"status": "dry_run", "body": body, "vk_method": "wall.post"},
        )
        await session.commit()
        return {"planned": 1, "published": 0, "districts": len(picked)}

    ok = await _publish_one(
        session,
        publish=publish,
        channel="oblast_digest",
        group_id=oblast_group_id,
        target_region_id=oblast_region.id,
        slot_key=slot_key,
        body=body,
    )
    return {"planned": 1, "published": 1 if ok else 0, "districts": len(picked)}


async def _has_pending_work(session, *, now: datetime) -> bool:
    """Есть ли кого продвигать. Пусто = не инцидент, а нормальная тишина."""
    count = (
        await session.execute(
            select(func.count(PromoEnrollment.id)).where(PromoEnrollment.status == "active")
        )
    ).scalar()
    return int(count or 0) > 0


async def maybe_alert_stale_promo(
    session,
    *,
    telegram_token: Optional[str] = None,
    chat_id: Optional[str] = None,
    dashboard_url: Optional[str] = None,
    now: Optional[datetime] = None,
) -> str:
    """Watchdog: алёрт, если модуль включён и боевой, а действий давно нет.

    Молчит в четырёх случаях, и каждый — не инцидент: модуль выключен флагом;
    все каналы в сухом прогоне (это режим этапа 1, а не поломка); продвигать
    некого; действия есть. Watchdog, который орёт на штатную тишину, обучает
    игнорировать себя — и промолчит тогда, когда сломается по-настоящему.
    """
    now = now or _now_msk()

    settings = (await session.execute(select(PromoSettings).limit(1))).scalar_one_or_none()
    if settings is None:
        return "skipped:no-settings"
    if promo_disabled():
        return "skipped:module-disabled"
    if module_paused(settings.paused_until, now=datetime.utcnow()):
        return "skipped:paused"

    live = any(
        resolve_channel(name, settings.channels, module_disabled=False, paused=False).publishes
        for name in ("promo_post", "oblast_digest")
    )
    if not live:
        return "skipped:dry-run"

    if not await _has_pending_work(session, now=now):
        return "no-targets"

    cutoff = datetime.utcnow() - timedelta(days=STALE_DAYS)
    last = (
        await session.execute(
            select(func.max(PromoAction.published_at)).where(PromoAction.status == "published")
        )
    ).scalar()
    if last is not None and last >= cutoff:
        return "fresh"
    if last is None:
        # Ни одного успешного действия за всю историю — «свежий запуск» и
        # «сломано навсегда» отсюда неразличимы, поэтому молчим.
        return "unknown:never-published"

    if not telegram_token or not chat_id:
        return "skipped:no-telegram-config"

    client = _redis()
    try:
        if client is not None and client.get(COOLDOWN_KEY):
            return "skipped:cooldown"
    except Exception:  # pragma: no cover
        pass

    days = (datetime.utcnow() - last).days
    message = (
        "⚠️ <b>SETKA: раскрутка встала</b>\n\n"
        f"Каналы в боевом режиме, районы в раскрутке есть, а последняя публикация "
        f"была <b>{days} сут назад</b> (порог {STALE_DAYS}).\n\n"
        "Проверь: <code>systemctl status setka-celery-beat setka-celery-worker</code>."
    )
    if dashboard_url:
        message += f"\n🔗 <a href='{dashboard_url}'>Открыть раскрутку</a>"

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
            logger.warning("stale-promo alert failed: %s %s", resp.status_code, resp.text[:200])
            return "error:http-" + str(resp.status_code)
        if client is not None:
            client.setex(COOLDOWN_KEY, ALERT_COOLDOWN_SECONDS, "1")
        return "alert-sent"
    except Exception as exc:  # pragma: no cover - сеть
        logger.error("Failed to send stale-promo alert: %s", exc)
        return "error:" + str(exc)
