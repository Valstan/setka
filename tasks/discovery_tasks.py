"""Celery wrapper + async core for region community discovery.

The heavy lifting lives in ``run_discovery_for_region_async`` so the web
endpoint can call it directly inside the FastAPI loop without going through
Celery (the wizard wants a synchronous «launch → I see candidates» UX).
The Celery wrapper is provided for future scheduled / batched runs.

Не вызывает Celery beat по умолчанию — нет шедула в `tasks/celery_app.py`.
Запуск ad-hoc через
``app.send_task('tasks.discovery_tasks.run_discovery_for_region', args=[region_id])``
или через POST `/api/discovery/trigger`.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import select

from config.runtime import VK_TOKENS
from database.connection import AsyncSessionLocal
from database.models import Community, CommunityCandidate, Region
from modules.discovery.ai_categorizer import categorize_candidate
from modules.discovery.vk_search import DiscoveredGroup, discover_for_region
from modules.vk_monitor.vk_client import VKClient

logger = logging.getLogger(__name__)


def _pick_parse_token() -> Optional[str]:
    """Return a VK token suitable for parse-side calls (groups.search etc.).

    VK_TOKENS — dict загруженный из env (`VK_TOKEN_VALSTAN`, `VK_TOKEN_VITA`).
    Возвращаем первый непустой; токенов хватает на одну discovery-серию
    (lim ~1000 groups.search/сутки на токен).
    """
    for name, tok in (VK_TOKENS or {}).items():
        if tok:
            logger.debug("discovery: using token %s", name)
            return tok
    return None


async def _existing_vk_ids(session, region_id: int) -> set[int]:
    """Vk_ids already in this region: established communities + previously
    rejected candidates. Used to skip wasteful re-discovery / re-AI.

    Не исключаем ``pending`` / ``deferred`` кандидатов — UI хочет refresh'ить
    их (AI-score мог измениться, появилась более свежая активность). Так что
    они получают ``ON CONFLICT DO UPDATE`` ниже.
    """
    q1 = await session.execute(select(Community.vk_id).where(Community.region_id == region_id))
    q2 = await session.execute(
        select(CommunityCandidate.vk_id).where(
            CommunityCandidate.region_id == region_id,
            CommunityCandidate.status == "rejected",
        )
    )
    out: set[int] = set()
    for (vk_id,) in q1.all():
        if vk_id is not None:
            out.add(abs(int(vk_id)))
    for (vk_id,) in q2.all():
        if vk_id is not None:
            out.add(abs(int(vk_id)))
    return out


async def _ai_categorize_all(
    groups: Sequence[DiscoveredGroup],
    region_name: str,
    *,
    max_concurrent: int = 4,
) -> Dict[int, Dict[str, Any]]:
    """Run ai_categorizer for every group, bounded concurrency.

    ``max_concurrent=4`` — Groq free tier обычно держит небольшой parallel.
    Возвращает map ``{vk_id: ai_result_dict}``. Failures остаются в map с
    ``success: False`` — caller сам решит сохранять с ai_*=NULL или пропустить.
    """
    semaphore = asyncio.Semaphore(max_concurrent)

    async def _one(g: DiscoveredGroup) -> tuple[int, Dict[str, Any]]:
        async with semaphore:
            res = await categorize_candidate(
                name=g.name,
                description=g.description,
                members_count=g.members_count,
                recent_posts=g.recent_posts,
                region_name=region_name,
            )
        return g.vk_id, res

    tasks = [_one(g) for g in groups]
    results = await asyncio.gather(*tasks, return_exceptions=False)
    return dict(results)


async def _upsert_candidates(
    session,
    region_id: int,
    groups: Sequence[DiscoveredGroup],
    ai_by_vk_id: Dict[int, Dict[str, Any]],
) -> Dict[str, int]:
    """Upsert discovered groups into community_candidates.

    Стратегия:
    - Новая запись (нет такой `(region_id, vk_id)`) → INSERT со status='pending'.
    - Существующая `pending` / `deferred` → UPDATE snapshot + ai_* (refresh).
    - Существующая `approved` / `rejected` → не трогать (модератор уже решил).

    Возвращает счётчики для отчёта.
    """
    inserted = 0
    refreshed = 0
    skipped = 0

    # One round-trip to fetch all existing candidates for this region.
    q = await session.execute(
        select(CommunityCandidate).where(
            CommunityCandidate.region_id == region_id,
            CommunityCandidate.vk_id.in_([g.vk_id for g in groups]),
        )
    )
    existing = {c.vk_id: c for c in q.scalars().all()}

    now = datetime.utcnow()
    for g in groups:
        ai = ai_by_vk_id.get(g.vk_id) or {}
        ai_ok = bool(ai.get("success"))
        cat = ai.get("category") if ai_ok else None
        conf = ai.get("confidence") if ai_ok else None
        reasoning = ai.get("reasoning") if ai_ok else None
        is_info = bool(ai.get("is_info_page")) if ai_ok else False

        row = existing.get(g.vk_id)
        if row is None:
            row = CommunityCandidate(
                region_id=region_id,
                vk_id=g.vk_id,
                name=g.name,
                screen_name=g.screen_name,
                photo_url=g.photo_url,
                description=g.description,
                members_count=g.members_count,
                ai_category=cat,
                ai_confidence=conf,
                ai_reasoning=reasoning,
                ai_is_info_page=is_info,
                status="pending",
                discovered_via=g.discovered_via,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            inserted += 1
        elif row.status in ("pending", "deferred"):
            # Refresh snapshot + AI fields. Не двигаем status.
            row.name = g.name or row.name
            row.screen_name = g.screen_name or row.screen_name
            row.photo_url = g.photo_url or row.photo_url
            if g.description is not None:
                row.description = g.description
            if g.members_count is not None:
                row.members_count = g.members_count
            if ai_ok:
                row.ai_category = cat
                row.ai_confidence = conf
                row.ai_reasoning = reasoning
                row.ai_is_info_page = is_info
            row.discovered_via = g.discovered_via or row.discovered_via
            row.updated_at = now
            refreshed += 1
        else:
            skipped += 1

    await session.commit()
    return {"inserted": inserted, "refreshed": refreshed, "skipped_existing": skipped}


async def run_discovery_for_region_async(
    region_id: int,
    *,
    categories: Optional[Sequence[str]] = None,
    per_query_count: int = 100,
) -> Dict[str, Any]:
    """Async core. Pure on top of session — no Celery dependency.

    Used both by the Celery task (via ``asyncio.run``) and directly by the
    FastAPI handler.

    Returns a structured report ``{success, region, found, inserted, refreshed,
    skipped_existing, skipped_ai_failed, ai_model}``.
    """
    async with AsyncSessionLocal() as session:
        region: Optional[Region] = (
            await session.execute(select(Region).where(Region.id == region_id))
        ).scalar_one_or_none()
        if region is None:
            return {"success": False, "error": f"region {region_id} not found"}
        if not region.center_city:
            return {
                "success": False,
                "error": "region.center_city is empty — set it before running discovery",
                "region": region.code,
            }
        exclude_ids = await _existing_vk_ids(session, region_id)

    token = _pick_parse_token()
    if not token:
        return {"success": False, "error": "no VK parse-token configured (VK_TOKENS empty)"}

    client = VKClient(token=token)

    # search_groups + get_groups_by_ids — sync; не блокируем event loop.
    groups: List[DiscoveredGroup] = await asyncio.to_thread(
        discover_for_region,
        client=client,
        center_city=region.center_city,
        vk_city_id=region.vk_city_id,
        categories=categories,
        per_query_count=per_query_count,
        exclude_vk_ids=exclude_ids,
    )

    if not groups:
        return {
            "success": True,
            "region": region.code,
            "found": 0,
            "inserted": 0,
            "refreshed": 0,
            "skipped_existing": 0,
            "skipped_ai_failed": 0,
        }

    ai_results = await _ai_categorize_all(groups, region.name)
    ai_failed = sum(1 for r in ai_results.values() if not r.get("success"))

    async with AsyncSessionLocal() as session:
        counts = await _upsert_candidates(session, region_id, groups, ai_results)

    return {
        "success": True,
        "region": region.code,
        "found": len(groups),
        "inserted": counts["inserted"],
        "refreshed": counts["refreshed"],
        "skipped_existing": counts["skipped_existing"],
        "skipped_ai_failed": ai_failed,
    }


# ─── Celery wrapper (для будущего шедулирования) ───
# Импортируем app только тут, чтобы тесты на async-core не тащили Celery.

try:
    from tasks.celery_app import app as _celery_app
    from utils.celery_asyncio import run_coro as _run_coro

    @_celery_app.task(name="tasks.discovery_tasks.run_discovery_for_region")
    def run_discovery_for_region(region_id: int, categories: Optional[List[str]] = None):
        """Celery task: запускает discovery для одного региона."""
        return _run_coro(run_discovery_for_region_async(region_id, categories=categories))

except Exception as _import_err:  # pragma: no cover
    # При локальном импорте без Celery (например, в тестах web-API)
    # Celery wrapper необязателен.
    logger.debug("Celery wrapper not registered: %s", _import_err)
