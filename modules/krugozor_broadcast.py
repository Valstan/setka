"""Поток «Кругозор»: научпоп-ДАЙДЖЕСТ веером на стены всех регионов.

Решение владельца 2026-06-14: познавательное/научпоп публикуется в районных пабликах
для расширения кругозора — «разносол» между местными новостями. Источники — сообщества
`category='krugozor'` (SciTopus, НауЧпок, ПостНаука, N+1, Образовач, TechInsider, Arzamas,
Антропогенез, Кот Шрёдингера, Наука и жизнь, Batrachospermum, Время-Вперёд и т.д.).

Механика (раз в день, beat):
- **Дайджест из РАЗНЫХ источников** (решение владельца): за прогон собираем до N (дефолт 4)
  свежих постов из РАЗНЫХ источников в один пост — «сколько влезёт» по бюджету длины. Выбор
  источников — ротацией (round-robin курсор), чтобы со временем все попали в эфир, а не
  только частопостящие (Время-Вперёд/N+1).
- **Каждый пункт**: «📚 Имя\n<текст или анонс ~500 знаков>…\n🔗 ссылка». Короткий пост —
  целиком; длинный — анонс + ссылка «читать». Лид-фото каждого пункта → грид под текстом.
- **Веер**: дайджест публикуется нативным постом на стены всех целевых регионов с паузой
  между публикациями (анти-Captcha).

Отдельный модуль (НЕ трогаем фрагильный copy_setka). Гейт `KRUGOZOR_BROADCAST_DISABLED`
(OFF по умолчанию, #008). Дедуп — по source-lip (cap 60, больше пунктов/прогон).

State в WorkTable(region_code="copy", theme="krugozor"):
  lip:  list — разосланные source-lip'ы (дедуп)
  hash: dict — {"rr": <курсор ротации источников>}
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
LIP_HISTORY_MAX = 60
HEADER = "🔭 Научпоп-сводка"


def _empty_stats() -> Dict[str, int]:
    return {"sources": 0, "targets": 0, "items": 0, "published": 0}


def _is_promo(post: Dict[str, Any]) -> bool:
    """Высокоточная проверка «это реклама/промо». Чистая.

    Только надёжные сигналы — официальная VK-метка `marked_as_ads` + легальные
    рекламные маркеры (`erid:`/`#реклама`/«на правах рекламы»), переиспользуем
    список из AdvertisementFilter. НАМЕРЕННО без commercial-scoring (цена/руб/
    купить/скидка): он тюнингован под локальные объявления и ложно бил бы по
    научному тексту. Консервативно: лучше пропустить редкий промо, чем выкинуть
    научпоп из дайджеста."""
    if not isinstance(post, dict):
        return False
    if post.get("marked_as_ads"):
        return True
    from modules.filters.ads_filter import AdvertisementFilter

    text = (post.get("text") or "").lower()
    return any(m.lower() in text for m in AdvertisementFilter.LEGAL_MARKERS)


def _newest_unseen(
    posts: List[Dict[str, Any]],
    seen: Set[str],
    max_age_seconds: int,
    now_ts: int,
    reject=None,
) -> Optional[Dict[str, Any]]:
    """Самый свежий пост источника, которого ещё не рассылали и не протух. Чистая.

    `reject` — опц. предикат (post) -> bool: True → пропустить этот пост (напр.
    анти-промо фильтр) и взять следующий свежий."""
    from utils.post_utils import lip_of_post

    for p in sorted(posts, key=lambda x: x.get("date", 0), reverse=True):
        pid = p.get("id")
        oid = p.get("owner_id")
        if pid is None or oid is None:
            continue
        if lip_of_post(int(oid), int(pid)) in seen:
            continue
        post_date = int(p.get("date") or 0)
        if max_age_seconds > 0 and now_ts - post_date > max_age_seconds:
            continue
        if reject is not None and reject(p):
            continue
        return p
    return None


def _rotation_order(n: int, rr: int) -> List[int]:
    """Порядок обхода источников, начиная со следующего за курсором rr. Чистая.

    rr — индекс последнего обработанного источника; начинаем с rr+1, идём по кругу."""
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


def _clean_text(text: str) -> str:
    """Схлопнуть лишние пустые строки/хвостовые пробелы. Чистая."""
    lines = [ln.rstrip() for ln in (text or "").strip().splitlines()]
    out = "\n".join(lines)
    while "\n\n\n" in out:
        out = out.replace("\n\n\n", "\n\n")
    return out.strip()


def _make_block(name: str, url: str, text: str, snippet_len: int) -> str:
    """Один пункт дайджеста: имя источника + текст/анонс + ссылка. Чистая."""
    t = _clean_text(text)
    if len(t) > snippet_len:
        cut = t[:snippet_len].rsplit(" ", 1)[0].rstrip(" ,.;:—-\n")
        t = (cut or t[:snippet_len]) + "…"
    body = (t + "\n") if t else ""
    return f"📚 {name}\n{body}🔗 {url}"


def _assemble_digest(
    items: List[Dict[str, Any]],
    *,
    snippet_len: int,
    text_budget: int,
    max_items: int,
    photos_enabled: bool,
) -> Tuple[str, List[str], List[int]]:
    """Собрать дайджест: текст + грид лид-фото + индексы вошедших пунктов. Чистая.

    items: [{name, url, text, photo}] (уже отсортированы как надо). «Сколько влезёт» —
    добираем, пока не упёрлись в max_items или в бюджет длины (первый пункт — всегда)."""
    blocks: List[str] = []
    attachments: List[str] = []
    used: List[int] = []
    total = len(HEADER)
    for i, it in enumerate(items):
        if len(used) >= max_items:
            break
        block = _make_block(it.get("name", ""), it.get("url", ""), it.get("text", ""), snippet_len)
        add_len = len(block) + 2  # разделитель "\n\n"
        if used and total + add_len > text_budget:
            break
        blocks.append(block)
        total += add_len
        used.append(i)
        if photos_enabled and it.get("photo"):
            attachments.append(it["photo"])
    text = HEADER + "\n\n" + "\n\n".join(blocks)
    return text, attachments, used


async def _get_krugozor_sources(session: AsyncSession) -> List[Dict[str, Any]]:
    """Активные сообщества-источники научпопа, упорядоченные стабильно (по vk_id)."""
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
            oid = -oid
        if oid in exclude:
            continue
        out.append({"owner_id": oid, "name": name or ""})
    return out


async def _read_wall(parse_tokens: Dict[str, str], owner_id: int) -> List[Dict[str, Any]]:
    """Прочитать стену источника, перебирая активные READ-токены (приватная группа /
    token-в-cooldown → пробуем следующий)."""
    from modules.vk_monitor.vk_client import VKClient

    for tok in parse_tokens.values():
        fetched = await asyncio.to_thread(
            VKClient(tok).get_wall_posts, owner_id, WALL_FETCH_COUNT, 0
        )
        if fetched:
            return fetched
    return []


def _lead_photo(post: Dict[str, Any]) -> Optional[str]:
    """Первое фото поста как attachment-строка ('photoX_Y'); None если фото нет."""
    from utils.vk_attachments import build_attachments_list, extract_vk_attachments

    att = extract_vk_attachments(post)
    photos = build_attachments_list({"photo": att.get("photo", [])}, max_items=1)
    return photos[0] if photos else None


async def execute_krugozor_broadcast(
    session: AsyncSession,
    *,
    test_mode: bool = False,
) -> Dict[str, Any]:
    from config.runtime import (
        get_krugozor_digest_max_items,
        get_krugozor_max_post_age_hours,
        get_krugozor_post_interval_seconds,
        get_krugozor_snippet_len,
        get_krugozor_target_region_codes,
        get_krugozor_text_budget,
        krugozor_broadcast_disabled,
        krugozor_digest_photos_enabled,
        krugozor_promo_filter_enabled,
    )
    from database.models import Region
    from database.models_extended import WorkTable
    from modules.publisher.vk_publisher_extended import VKPublisher
    from modules.vk_token_router import get_active_parse_tokens
    from utils.post_utils import clear_copy_history, lip_of_post

    if krugozor_broadcast_disabled():
        logger.info("KRUGOZOR_BROADCAST_DISABLED — поток «Кругозор» пропущен")
        return {"success": False, "skipped": True, "error": "disabled", "stats": _empty_stats()}

    parse_tokens = await get_active_parse_tokens(session)
    if not parse_tokens:
        return {"success": False, "error": "No active VK READ tokens", "stats": _empty_stats()}

    sources = await _get_krugozor_sources(session)
    if not sources:
        return {"success": True, "message": "no krugozor sources", "stats": _empty_stats()}

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
    seen: Set[str] = set(wt.lip or [])

    max_items = get_krugozor_digest_max_items()
    max_age = int(get_krugozor_max_post_age_hours() * 3600)
    now_ts = int(time.time())

    # Сбор кандидатов: идём ротацией, по одному свежему посту с каждого источника,
    # пока не наберём max_items (разных источников) или не обойдём всех.
    reject = _is_promo if krugozor_promo_filter_enabled() else None
    candidates: List[Dict[str, Any]] = []
    last_read_idx = rr
    picked_lips: Set[str] = set()
    for idx in _rotation_order(len(sources), rr):
        if len(candidates) >= max_items:
            break
        last_read_idx = idx
        src = sources[idx]
        posts = await _read_wall(parse_tokens, int(src["owner_id"]))
        if not posts:
            continue
        fresh = _newest_unseen(posts, seen | picked_lips, max_age, now_ts, reject=reject)
        if fresh is None:
            continue
        oid, pid = int(fresh.get("owner_id")), int(fresh["id"])
        lip = lip_of_post(oid, pid)
        effective = clear_copy_history(copy_lib.deepcopy(fresh))
        candidates.append(
            {
                "name": str(src.get("name") or "").strip() or "источник",
                "url": f"https://vk.com/wall{oid}_{pid}",
                "text": effective.get("text") or "",
                "photo": _lead_photo(effective),
                "lip": lip,
                "date": int(fresh.get("date") or 0),
            }
        )
        picked_lips.add(lip)

    if not candidates:
        return {"success": True, "message": "no fresh posts to digest", "stats": _empty_stats()}

    # Свежайшее — первым пунктом дайджеста.
    candidates.sort(key=lambda c: c["date"], reverse=True)
    text, attachments, used = _assemble_digest(
        candidates,
        snippet_len=get_krugozor_snippet_len(),
        text_budget=get_krugozor_text_budget(),
        max_items=max_items,
        photos_enabled=krugozor_digest_photos_enabled(),
    )
    selected = [candidates[i] for i in used]

    publisher = await VKPublisher.create_with_policy(
        session, target_group_id=None, test_polygon_mode=test_mode
    )
    interval = get_krugozor_post_interval_seconds()
    published, errors = 0, []
    for i, reg in enumerate(regions):
        if i > 0 and interval > 0:
            await asyncio.sleep(interval)
        try:
            res = await publisher.publish_digest(
                group_id=int(reg.vk_group_id), text=text, attachments=attachments
            )
            if res.get("success"):
                published += 1
                logger.info("krugozor: дайджест -> %s OK %s", reg.code, res.get("url"))
            else:
                errors.append(f"{reg.code}: {res.get('error', 'unknown')}")
        except Exception as e:  # noqa: BLE001 — изолируем сбой одного региона
            logger.exception("krugozor: failed for %s", reg.code)
            errors.append(f"{reg.code}: {e}")

    # Помечаем разосланными ТОЛЬКО если хоть куда-то опубликовали (иначе повторим).
    if published > 0:
        for it in selected:
            _mark_seen(wt, it["lip"])
        wt.hash = {"rr": last_read_idx}
        await session.commit()

    logger.info(
        "krugozor: дайджест из %d пунктов → %d/%d регионов (источники: %s)",
        len(selected),
        published,
        len(regions),
        ", ".join(it["name"] for it in selected),
    )
    return {
        "success": published > 0,
        "items": len(selected),
        "sources_in_digest": [it["name"] for it in selected],
        "posts_published": published,
        "targets": len(regions),
        "n_attachments": len(attachments),
        "errors": errors[:20],
        "stats": {
            "sources": len(sources),
            "targets": len(regions),
            "items": len(selected),
            "published": published,
        },
    }
