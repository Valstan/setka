"""Поток «Кругозор»: научпоп/познавательное → веером на стены всех регионов.

Решение владельца 2026-06-14 (по итогам precision-спот-чека LLM-курации): новости
науки и оптимистичное должны публиковаться в районных пабликах для расширения
кругозора — «разносол» между местными новостями. Источники — уцелевшие от Постопуса
сообщества `category='krugozor'` (SciTopus, НауЧпок, Batrachospermum, Время-Вперёд).

Механика (раз в день, beat):
- **Ротация источников** (round-robin) — каждый прогон берёт свежий пост СЛЕДУЮЩЕГО
  источника, чтобы был разносол и Время-Вперёд не флудил (он постит часто).
- **Копи-режим, не репост** (решение владельца): копируем текст+фото в НАТИВНЫЙ пост
  каждого района (полный охват умной ленты, статистика идёт целевому паблику) +
  native-атрибуция VK (`copyright` = ссылка на пост-источник). Видео перезалить нельзя
  → деградирует в ссылку (как и в copy_setka).
- **Веер с добором**: рассылаем только тем регионам, кто ещё не получил пост; пауза
  между публикациями (анти-Captcha); недоставленные регионы добираются на след. тиках.

Отдельный модуль (НЕ трогаем фрагильный copy_setka — у него single-source модель):
переиспользуем его проверенные кубики (VKPublisher/роутинг токенов, копирование
вложений, паттерн добора, WorkTable-состояние). Гейт `KRUGOZOR_BROADCAST_DISABLED`
(OFF по умолчанию, #008) — рассылка молчит, пока владелец не включит env.

State в WorkTable(region_code="copy", theme="krugozor"):
  lip:  list — разосланные source-lip'ы (дедуп, cap 40)
  hash: dict — {"rr": <курсор ротации>, "pending": {"lip","done","tries"} | None}
"""

from __future__ import annotations

import asyncio
import copy as copy_lib
import logging
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

WALL_FETCH_COUNT = 10
LIP_HISTORY_MAX = 40
PENDING_MAX_TRIES = 4
SOURCE_FOOTER = "🔭 {name}"  # имя источника под текстом (native-ссылку даёт copyright)


def _empty_stats() -> Dict[str, int]:
    return {"sources": 0, "targets": 0, "published": 0}


def _newest_unseen(
    posts: List[Dict[str, Any]],
    seen: Set[str],
    max_age_seconds: int,
    now_ts: int,
) -> Optional[Dict[str, Any]]:
    """Самый свежий пост источника, которого ещё не рассылали и не протух. Чистая."""
    from utils.post_utils import lip_of_post

    for p in sorted(posts, key=lambda x: x.get("date", 0), reverse=True):
        pid = p.get("id")
        if pid is None:
            continue
        oid = p.get("owner_id")
        if oid is None:
            continue
        lip = lip_of_post(int(oid), int(pid))
        if lip in seen:
            continue
        post_date = int(p.get("date") or 0)
        if max_age_seconds > 0 and now_ts - post_date > max_age_seconds:
            continue
        return p
    return None


def _rotation_order(n: int, rr: int) -> List[int]:
    """Порядок обхода источников, начиная со следующего за курсором rr. Чистая.

    rr — индекс последнего опубликованного источника; начинаем с rr+1, идём по кругу.
    Для n=4, rr=1 → [2, 3, 0, 1]."""
    if n <= 0:
        return []
    start = (rr + 1) % n
    return [(start + i) % n for i in range(n)]


def _mark_seen(wt: Any, lip: str) -> None:
    """Добавить source-lip в список разосланных (дедуп + cap)."""
    prev = list(wt.lip or [])
    if lip not in prev:
        prev.append(lip)
    wt.lip = prev[-LIP_HISTORY_MAX:]


def _build_footer(name: str) -> str:
    name = (name or "").strip()
    return ("\n\n" + SOURCE_FOOTER.format(name=name)) if name else ""


async def _get_krugozor_sources(session: AsyncSession) -> List[Dict[str, Any]]:
    """Активные сообщества-источники научпопа, упорядоченные стабильно (по vk_id).

    Стабильный порядок важен для ротации (rr-курсор ссылается на позицию)."""
    from config.runtime import get_krugozor_source_category, get_krugozor_source_exclude_ids
    from database.models import Community

    category = get_krugozor_source_category()
    exclude = get_krugozor_source_exclude_ids()
    rows = (
        await session.execute(
            select(Community.vk_id, Community.name)
            .where(Community.category == category, Community.is_active.is_(True))
            .order_by(Community.vk_id)
        )
    ).all()
    out: List[Dict[str, Any]] = []
    for vk_id, name in rows:
        oid = int(vk_id)
        if oid > 0:
            oid = -oid  # owner_id группы — отрицательный
        if oid in exclude:
            continue
        out.append({"owner_id": oid, "name": name or ""})
    return out


async def _read_wall(
    parse_tokens: Dict[str, str], owner_id: int
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Прочитать стену источника, перебирая активные READ-токены (как copy_setka:
    приватная группа / token-в-cooldown → пробуем следующий)."""
    from modules.vk_monitor.vk_client import VKClient

    for tok_name, tok in parse_tokens.items():
        fetched = await asyncio.to_thread(
            VKClient(tok).get_wall_posts, owner_id, WALL_FETCH_COUNT, 0
        )
        if fetched:
            return fetched, tok_name
    return [], None


async def _fetch_post_by_lip(token: str, lip: str) -> Optional[Dict[str, Any]]:
    """Дофетчить пост по lip, когда он уехал из последних N (для добора pending)."""
    from modules.vk_monitor.vk_client import VKClient

    try:
        abs_oid_s, pid_s = lip.split("_")
        owner, pid = -abs(int(abs_oid_s)), int(pid_s)
    except (ValueError, AttributeError):
        return None
    fetched = await asyncio.to_thread(VKClient(token).get_posts_by_ids, [(owner, pid)])
    return fetched[0] if fetched else None


async def execute_krugozor_broadcast(
    session: AsyncSession,
    *,
    test_mode: bool = False,
) -> Dict[str, Any]:
    from config.runtime import (
        get_krugozor_max_post_age_hours,
        get_krugozor_post_interval_seconds,
        get_krugozor_target_region_codes,
        krugozor_broadcast_disabled,
    )
    from database.models import Region
    from database.models_extended import WorkTable
    from modules.publisher.vk_publisher_extended import VKPublisher
    from modules.vk_token_router import get_active_parse_tokens
    from utils.post_utils import clear_copy_history, lip_of_post
    from utils.vk_attachments import build_attachments_list, extract_vk_attachments

    if krugozor_broadcast_disabled():
        logger.info("KRUGOZOR_BROADCAST_DISABLED — поток «Кругозор» пропущен")
        return {"success": False, "skipped": True, "error": "disabled", "stats": _empty_stats()}

    parse_tokens = await get_active_parse_tokens(session)
    if not parse_tokens:
        return {
            "success": False,
            "error": "No active VK READ tokens (all in cooldown?)",
            "stats": _empty_stats(),
        }

    sources = await _get_krugozor_sources(session)
    if not sources:
        return {"success": True, "message": "no krugozor sources", "stats": _empty_stats()}

    # Целевые регионы (все активные с vk_group_id, кроме псевдо-региона copy).
    region_filter = get_krugozor_target_region_codes()
    rq = select(Region).where(
        Region.is_active.is_(True),
        Region.vk_group_id.isnot(None),
        Region.code != "copy",
    )
    if region_filter:
        rq = rq.where(Region.code.in_(list(region_filter)))
    regions = list((await session.execute(rq)).scalars().all())
    if not regions:
        return {"success": False, "error": "no target regions", "stats": _empty_stats()}
    target_codes = {reg.code for reg in regions}

    # State (rr-курсор ротации + pending-добор).
    wt = (
        (
            await session.execute(
                select(WorkTable).where(
                    WorkTable.region_code == "copy", WorkTable.theme == "krugozor"
                )
            )
        )
        .scalars()
        .first()
    )
    if not wt:
        wt = WorkTable(region_code="copy", theme="krugozor", lip=[], hash=[])
        session.add(wt)
        await session.commit()
        await session.refresh(wt)
    state = wt.hash if isinstance(wt.hash, dict) else {}
    rr = int(state.get("rr", -1))
    pending = state.get("pending") if isinstance(state.get("pending"), dict) else None
    seen: Set[str] = set(wt.lip or [])

    candidate: Optional[Dict[str, Any]] = None
    source_name: str = ""
    done_codes: Set[str] = set()
    pending_tries = 0
    chosen_idx = rr

    # --- 1) Сначала добираем недоставленный pending-пост ----------------------
    if pending and pending.get("lip"):
        plip = str(pending["lip"])
        pdone = {str(c) for c in (pending.get("done") or [])}
        if target_codes <= pdone:
            _mark_seen(wt, plip)
            state["pending"] = None
            wt.hash = dict(state)
            await session.commit()
        else:
            any_token = next(iter(parse_tokens.values()))
            cand = await _fetch_post_by_lip(any_token, plip)
            if cand is None:
                state["pending"] = None
                wt.hash = dict(state)
                await session.commit()
            else:
                candidate = cand
                source_name = str(pending.get("source_name") or "")
                done_codes = pdone
                pending_tries = int(pending.get("tries") or 0)

    # --- 2) Нет pending → берём свежий пост следующего по ротации источника ----
    if candidate is None:
        max_age = int(get_krugozor_max_post_age_hours() * 3600)
        now_ts = int(time.time())
        for idx in _rotation_order(len(sources), rr):
            src = sources[idx]
            posts, _tok = await _read_wall(parse_tokens, int(src["owner_id"]))
            if not posts:
                continue
            fresh = _newest_unseen(posts, seen, max_age, now_ts)
            if fresh is not None:
                candidate = fresh
                source_name = str(src.get("name") or "")
                chosen_idx = idx
                break

    if candidate is None:
        return {"success": True, "message": "no fresh post to propagate", "stats": _empty_stats()}

    src_oid = int(candidate.get("owner_id"))
    src_pid = int(candidate["id"])
    src_lip = lip_of_post(src_oid, src_pid)
    source_url = f"https://vk.com/wall{src_oid}_{src_pid}"

    # Копи-режим: текст исходного поста (разворачиваем repost-цепочку) + фото.
    raw = copy_lib.deepcopy(candidate)
    effective = clear_copy_history(raw)
    body_text = (effective.get("text") or "").strip()
    att_dict = extract_vk_attachments(effective)
    copy_attachments = build_attachments_list(att_dict, max_items=10)
    out_text = body_text + _build_footer(source_name)

    publisher = await VKPublisher.create_with_policy(
        session, target_group_id=None, test_polygon_mode=test_mode
    )
    interval = get_krugozor_post_interval_seconds()

    targets_remaining = [reg for reg in regions if reg.code not in done_codes]
    newly_done: Set[str] = set()
    errors: List[str] = []
    for idx, reg in enumerate(targets_remaining):
        if idx > 0 and interval > 0:
            await asyncio.sleep(interval)
        try:
            res = await publisher.publish_digest(
                group_id=int(reg.vk_group_id),
                text=out_text,
                attachments=copy_attachments,
                copyright_url=source_url,
            )
            if res.get("success"):
                newly_done.add(reg.code)
                logger.info("krugozor: %s -> %s OK %s", src_lip, reg.code, res.get("url"))
            else:
                errors.append(f"{reg.code}: {res.get('error', 'unknown')}")
        except Exception as e:  # noqa: BLE001 — изолируем сбой одного региона
            logger.exception("krugozor: failed for %s", reg.code)
            errors.append(f"{reg.code}: {e}")

    all_done = done_codes | newly_done
    complete = target_codes <= all_done
    missing = sorted(target_codes - all_done)

    # rr-курсор двигаем на опубликованный источник только когда пост закрыт
    # (доставлен всем или сдались), чтобы добор pending не «съедал» ротацию.
    if complete:
        _mark_seen(wt, src_lip)
        state = {"rr": chosen_idx, "pending": None}
        logger.info("krugozor: пост %s доставлен всем %d регионам", src_lip, len(target_codes))
    else:
        tries = pending_tries + 1
        if tries >= PENDING_MAX_TRIES:
            _mark_seen(wt, src_lip)
            state = {"rr": chosen_idx, "pending": None}
            logger.warning(
                "krugozor: пост %s не добран за %d попыток — сдаюсь; пропущены: %s",
                src_lip,
                tries,
                ", ".join(missing),
            )
        else:
            state = {
                "rr": rr,  # ротацию НЕ двигаем, пока пост не закрыт
                "pending": {
                    "lip": src_lip,
                    "done": sorted(all_done),
                    "tries": tries,
                    "source_name": source_name,
                },
            }
            logger.info(
                "krugozor: пост %s разослан частично (%d/%d), добор на след. тике",
                src_lip,
                len(all_done),
                len(target_codes),
            )
    wt.hash = dict(state)
    await session.commit()

    return {
        "success": complete or len(newly_done) > 0,
        "posts_published": len(newly_done),
        "source_lip": src_lip,
        "source_name": source_name,
        "source_url": source_url,
        "mode": "wall.post copy",
        "targets": len(targets_remaining),
        "complete": complete,
        "missing": missing,
        "errors": errors[:20],
        "stats": {
            "sources": len(sources),
            "targets": len(target_codes),
            "published": len(newly_done),
        },
    }
