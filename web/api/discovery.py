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
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, validator
from sqlalchemy import select

from config.runtime import VK_TOKENS
from database.connection import AsyncSessionLocal
from database.models import Community, CommunityCandidate
from modules.discovery.ai_categorizer import ALLOWED_CATEGORIES
from modules.vk_monitor.vk_client import VKClient
from tasks.discovery_tasks import run_discovery_for_region_async

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
    status: str  # approved / rejected / deferred
    category: Optional[str] = None  # required when status='approved'

    @validator("status")
    def _valid_status(cls, v):
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
    """Approve / reject / defer a candidate.

    Approve без category → ошибка. Approve с category → создаёт `Community`
    (или обновляет если уже была) и помечает кандидата ``approved``.
    """
    async with AsyncSessionLocal() as session:
        cand = await session.get(CommunityCandidate, candidate_id)
        if cand is None:
            raise HTTPException(status_code=404, detail="candidate not found")

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
