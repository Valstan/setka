"""Discovery API — авто-регистрация регионов и сообществ (big idea 2026-05-22).

Endpoints:

- ``GET  /api/discovery/cities?q=…``       — VK ``database.getCities`` resolver
                                            для wizard'а нового региона.
- ``POST /api/discovery/trigger``           — запустить discovery для региона
                                            (синхронно, держим запрос пока
                                            не вернётся результат — UI wizard
                                            ждёт, обычно 10-60 сек).
- ``GET  /api/discovery/candidates?region_id=…`` — список кандидатов региона.
- ``PATCH /api/discovery/candidates/{cid}`` — approve / reject / defer.
- ``POST /api/discovery/candidates/bulk``   — массовая операция (фильтр
                                            по confidence / категории).

«Approve» создаёт запись в `communities` (через composite unique
``(region_id, vk_id)``) и помечает candidate как ``approved``.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, validator
from sqlalchemy import select

from config.runtime import VK_TOKENS
from database.connection import AsyncSessionLocal
from database.models import Community, CommunityCandidate, Region
from modules.discovery.ai_categorizer import ALLOWED_CATEGORIES
from modules.vk_monitor.vk_client import VKClient
from tasks.discovery_tasks import run_discovery_for_region_async
from utils.vk_url import parse_vk_group_url

logger = logging.getLogger(__name__)
router = APIRouter()

_VALID_STATUSES = {"pending", "approved", "rejected", "deferred"}


# ─────────────────────────────────────────────────────────────────
# /cities — VK database.getCities resolver
# ─────────────────────────────────────────────────────────────────


@router.get("/cities")
async def resolve_city(q: str = Query(..., min_length=1, max_length=120)):
    """Resolve human-readable city name → list of VK cities for dropdown."""
    token = next((t for t in (VK_TOKENS or {}).values() if t), None)
    if not token:
        raise HTTPException(status_code=503, detail="no VK parse-token configured")
    client = VKClient(token=token)
    items = client.resolve_city(query=q)
    # Trim payload to fields useful for UI.
    return {
        "items": [
            {
                "id": int(it.get("id") or 0),
                "title": it.get("title") or "",
                "area": it.get("area") or "",
                "region": it.get("region") or "",
            }
            for it in items
            if it.get("id")
        ]
    }


# ─────────────────────────────────────────────────────────────────
# /trigger — kick off discovery for a region
# ─────────────────────────────────────────────────────────────────


class TriggerIn(BaseModel):
    region_id: int
    categories: Optional[List[str]] = None  # subset of CATEGORY_KEYWORDS keys
    per_query_count: int = Field(default=100, ge=10, le=1000)

    @validator("categories", each_item=True)
    def _valid_cat(cls, v):
        if v not in ALLOWED_CATEGORIES:
            raise ValueError(f"unknown category: {v!r}")
        return v


@router.post("/trigger")
async def trigger_discovery(payload: TriggerIn):
    """Run discovery for one region. Synchronous — UI wizard ждёт результата."""
    try:
        result = await run_discovery_for_region_async(
            region_id=payload.region_id,
            categories=payload.categories,
            per_query_count=payload.per_query_count,
        )
    except Exception as e:
        logger.exception("discovery trigger failed")
        raise HTTPException(status_code=500, detail=str(e))
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error") or "discovery failed")
    return result


# ─────────────────────────────────────────────────────────────────
# /candidates — list / filter
# ─────────────────────────────────────────────────────────────────


@router.get("/candidates")
async def list_candidates(
    region_id: int = Query(..., ge=1),
    status: Optional[str] = Query(None),
    min_confidence: Optional[int] = Query(None, ge=0, le=100),
    only_info_pages: bool = Query(False),
):
    """List candidates for a region, filterable by status / confidence / flag."""
    if status and status not in _VALID_STATUSES:
        raise HTTPException(status_code=400, detail=f"invalid status {status!r}")

    async with AsyncSessionLocal() as session:
        stmt = (
            select(CommunityCandidate)
            .where(CommunityCandidate.region_id == region_id)
            .order_by(
                CommunityCandidate.ai_is_info_page.desc(),
                CommunityCandidate.ai_confidence.desc().nullslast(),
                CommunityCandidate.members_count.desc().nullslast(),
                CommunityCandidate.id.desc(),
            )
        )
        if status:
            stmt = stmt.where(CommunityCandidate.status == status)
        if min_confidence is not None:
            stmt = stmt.where(CommunityCandidate.ai_confidence >= min_confidence)
        if only_info_pages:
            stmt = stmt.where(CommunityCandidate.ai_is_info_page.is_(True))

        rows = (await session.execute(stmt)).scalars().all()
        return {"candidates": [r.to_dict() for r in rows], "count": len(rows)}


# ─────────────────────────────────────────────────────────────────
# PATCH /candidates/{id} — approve / reject / defer
# ─────────────────────────────────────────────────────────────────


class CandidatePatch(BaseModel):
    # И status, и category — опциональны. Допустимые комбинации:
    #   {status: approved, category: ...}  — одобрить с конкретной категорией
    #   {status: rejected}                 — отклонить
    #   {status: deferred}                 — отложить
    #   {category: ...}                    — только сменить AI-категорию
    #                                        (двух-этапный flow: модератор
    #                                        перетасовывает по тематикам до
    #                                        финального commit'а региона)
    status: Optional[str] = None
    category: Optional[str] = None

    @validator("status")
    def _valid_status(cls, v):
        if v is None:
            return None
        v = (v or "").strip().lower()
        if v not in {"approved", "rejected", "deferred"}:
            raise ValueError(f"invalid status {v!r}")
        return v

    @validator("category")
    def _valid_cat(cls, v):
        if v is None or v == "":
            return None
        if v not in ALLOWED_CATEGORIES:
            raise ValueError(f"unknown category {v!r}")
        return v


async def _approve_candidate(session, candidate: CommunityCandidate, category: str) -> Community:
    """Create a Community row for an approved candidate.

    Идемпотентность — на уровне ``(region_id, vk_id, category)``: одна VK-группа
    может жить в `communities` с разными category одновременно (см.
    database/migrations/011 — комментарий к idx_communities_region_vk).
    Если запись с такой тройкой уже есть, освежаем её; иначе INSERT.
    """
    existing: Optional[Community] = (
        await session.execute(
            select(Community)
            .where(
                Community.region_id == candidate.region_id,
                Community.vk_id == candidate.vk_id,
                Community.category == category,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.name = candidate.name or existing.name
        existing.screen_name = candidate.screen_name or existing.screen_name
        existing.is_active = True
        return existing
    community = Community(
        region_id=candidate.region_id,
        vk_id=candidate.vk_id,
        name=candidate.name,
        screen_name=candidate.screen_name,
        category=category,
        is_active=True,
        health_status="active",
    )
    session.add(community)
    return community


@router.patch("/candidates/{candidate_id}")
async def patch_candidate(candidate_id: int, payload: CandidatePatch):
    """Approve / reject / defer / re-categorise a candidate.

    Допустимые комбинации body — см. ``CandidatePatch``. Если задан только
    ``category`` без ``status`` — обновляем `ai_category` (для двух-этапного
    UI flow). Approve без конкретной category → 400.
    """
    if payload.status is None and payload.category is None:
        raise HTTPException(status_code=400, detail="body must include status and/or category")

    async with AsyncSessionLocal() as session:
        cand = await session.get(CommunityCandidate, candidate_id)
        if cand is None:
            raise HTTPException(status_code=404, detail="candidate not found")

        # Category-only patch — re-categorise (для inline-dropdown в UI).
        if payload.status is None:
            cand.ai_category = payload.category
            cand.updated_at = datetime.utcnow()
            await session.commit()
            await session.refresh(cand)
            return {"candidate": cand.to_dict()}

        new_status = payload.status
        if new_status == "approved":
            category = payload.category or cand.ai_category
            if not category or category not in ALLOWED_CATEGORIES or category == "other":
                raise HTTPException(
                    status_code=400,
                    detail="approve requires a concrete category (cannot be 'other' or empty)",
                )
            community = await _approve_candidate(session, cand, category)
            cand.status = "approved"
            await session.commit()
            await session.refresh(cand)
            await session.refresh(community)
            return {
                "candidate": cand.to_dict(),
                "community_id": community.id,
            }

        # reject / defer — просто обновляем статус.
        cand.status = new_status
        await session.commit()
        await session.refresh(cand)
        return {"candidate": cand.to_dict()}


# ─────────────────────────────────────────────────────────────────
# POST /candidates/bulk — массовые операции
# ─────────────────────────────────────────────────────────────────


class BulkPatch(BaseModel):
    region_id: int
    status: str  # approved / rejected / deferred
    min_confidence: Optional[int] = Field(default=None, ge=0, le=100)
    categories: Optional[List[str]] = None  # фильтр по AI category
    only_info_pages: bool = False

    @validator("status")
    def _valid_status(cls, v):
        v = (v or "").strip().lower()
        if v not in {"approved", "rejected", "deferred"}:
            raise ValueError(f"invalid status {v!r}")
        return v


@router.post("/candidates/bulk")
async def bulk_patch(payload: BulkPatch):
    """Bulk operation. For ``approved`` we ONLY auto-approve candidates whose
    ``ai_category`` is concrete (not None / not 'other') — иначе approve
    требует ручного выбора категории."""
    async with AsyncSessionLocal() as session:
        stmt = select(CommunityCandidate).where(
            CommunityCandidate.region_id == payload.region_id,
            CommunityCandidate.status == "pending",
        )
        if payload.min_confidence is not None:
            stmt = stmt.where(CommunityCandidate.ai_confidence >= payload.min_confidence)
        if payload.categories:
            stmt = stmt.where(CommunityCandidate.ai_category.in_(payload.categories))
        if payload.only_info_pages:
            stmt = stmt.where(CommunityCandidate.ai_is_info_page.is_(True))

        cands = (await session.execute(stmt)).scalars().all()

        if payload.status == "approved":
            approved_n = 0
            skipped_no_cat = 0
            for cand in cands:
                cat = cand.ai_category
                if not cat or cat == "other":
                    skipped_no_cat += 1
                    continue
                await _approve_candidate(session, cand, cat)
                cand.status = "approved"
                approved_n += 1
            await session.commit()
            return {
                "matched": len(cands),
                "approved": approved_n,
                "skipped_no_category": skipped_no_cat,
            }

        # rejected / deferred — массово.
        n = 0
        for cand in cands:
            cand.status = payload.status
            n += 1
        await session.commit()
        return {"matched": len(cands), "updated": n, "status": payload.status}


# ─────────────────────────────────────────────────────────────────
# /resolve-vk-url — превратить ссылку на VK-сообщество в (group_id, name)
# ─────────────────────────────────────────────────────────────────


@router.get("/resolve-vk-url")
async def resolve_vk_url(url: str = Query(..., min_length=1, max_length=500)):
    """Превратить URL/screen_name/ID VK-сообщества в `{group_id, name}`.

    Используется wizard'ом для поля «Главная группа региона». Если URL —
    screen_name, делает один VK API `utils.resolveScreenName` + `groups.getById`
    для подтверждения и получения title; если уже числовой club/public id —
    идёт сразу в `groups.getById`.
    """
    group_id, screen_name = parse_vk_group_url(url)
    if group_id is None and screen_name is None:
        raise HTTPException(status_code=400, detail="не удалось распознать VK-ссылку")

    token = next((t for t in (VK_TOKENS or {}).values() if t), None)
    if not token:
        raise HTTPException(status_code=503, detail="no VK parse-token configured")
    client = VKClient(token=token)

    if group_id is None and screen_name is not None:
        try:
            resolved = client.vk.utils.resolveScreenName(screen_name=screen_name)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"VK resolveScreenName failed: {e}")
        if not resolved or resolved.get("type") != "group":
            raise HTTPException(
                status_code=404,
                detail=f"VK не нашёл группу с адресом '{screen_name}'",
            )
        group_id = int(resolved.get("object_id") or 0)

    if not group_id:
        raise HTTPException(status_code=400, detail="не удалось определить group_id")

    try:
        infos = client.get_groups_by_ids([group_id], fields="screen_name,members_count,photo_200")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"VK groups.getById failed: {e}")
    if not infos:
        raise HTTPException(status_code=404, detail=f"VK group {group_id} не найден")
    info = infos[0]
    return {
        "group_id": group_id,
        "screen_name": info.get("screen_name") or screen_name,
        "name": info.get("name") or "",
        "members_count": info.get("members_count"),
        "photo_url": info.get("photo_200"),
    }


# ─────────────────────────────────────────────────────────────────
# /commit/{region_id} — финализировать черновик региона
# ─────────────────────────────────────────────────────────────────


@router.post("/commit/{region_id}")
async def commit_region(region_id: int):
    """Финализация двух-этапного flow создания региона.

    Что делает:
    1. Проверяет `region.vk_group_id NOT NULL` (без главной группы регион
       не попадёт в beat-расписание — см. `parsing_scheduler_tasks.py`).
    2. Bulk-approve всех **pending** кандидатов с `ai_category` ∈
       ALLOWED_CATEGORIES (кроме 'other') — создаёт `Community.is_active=True`
       для каждого через существующую `_approve_candidate` helper.
    3. Поднимает `region.is_active=True` (черновик → активный).
    4. Кандидаты, которых модератор перевёл в `rejected` / `deferred` — не
       трогаем. Остальные pending без подходящей категории остаются pending
       (модератор разберётся позже).

    Returns: ``{region_code, communities_created, pending_left, region_id}``.
    """
    async with AsyncSessionLocal() as session:
        region: Optional[Region] = (
            await session.execute(select(Region).where(Region.id == region_id))
        ).scalar_one_or_none()
        if region is None:
            raise HTTPException(status_code=404, detail="region not found")

        if not region.vk_group_id:
            raise HTTPException(
                status_code=400,
                detail="у региона не задана главная VK-группа (vk_group_id) — без неё "
                "он не попадёт в расписание парсинга",
            )

        cands = (
            (
                await session.execute(
                    select(CommunityCandidate).where(
                        CommunityCandidate.region_id == region_id,
                        CommunityCandidate.status == "pending",
                    )
                )
            )
            .scalars()
            .all()
        )

        approved_n = 0
        pending_left = 0
        for cand in cands:
            cat = cand.ai_category
            if not cat or cat == "other":
                pending_left += 1
                continue
            await _approve_candidate(session, cand, cat)
            cand.status = "approved"
            approved_n += 1

        if approved_n == 0:
            raise HTTPException(
                status_code=400,
                detail="нет ни одного кандидата с категорией для approve — "
                "распределите кандидатов по тематикам или используйте reject/defer",
            )

        if not region.is_active:
            region.is_active = True
        region.updated_at = datetime.utcnow()

        await session.commit()

        return {
            "region_id": region.id,
            "region_code": region.code,
            "communities_created": approved_n,
            "pending_left": pending_left,
        }
