"""API рассылки рекламного оффера (Этап 4): кампании, набор адресатов, тик, ручной список.

- ``GET  /campaigns`` / ``POST /campaigns`` / ``GET /campaigns/{id}``;
- ``POST /campaigns/{id}/enroll`` — набрать адресатов за N месяцев + завести кабинеты;
- ``POST /campaigns/{id}/start|pause|stop`` — статус; ``start`` принимает ``dry_run``;
- ``POST /campaigns/{id}/dispatch`` — тик руками (тот же код, что у beat);
- ``GET  /campaigns/{id}/recipients?status=`` — таблица; ``GET /campaigns/{id}/manual`` —
  ручной список (deeplink + текст); ``POST /recipients/{id}/done`` — «написал сам»;
- ``POST /blacklist`` — стоп-лист (человек больше не попадает в кампании).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.connection import get_db_session
from database.models import AdOutreachBlacklist, AdOutreachCampaign, AdOutreachRecipient
from modules.ad_cabinet import outreach

logger = logging.getLogger(__name__)
router = APIRouter()


class CampaignIn(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    template_id: Optional[int] = None
    months_back: int = Field(6, ge=1, le=24)
    per_community_daily: int = Field(30, ge=1, le=200)
    total_daily: int = Field(150, ge=1, le=1000)
    quiet_start: int = Field(21, ge=0, le=23)
    quiet_end: int = Field(9, ge=0, le=23)
    images: List[str] = Field(default_factory=list)
    note: Optional[str] = None


class StartIn(BaseModel):
    dry_run: bool = True


class BlacklistIn(BaseModel):
    vk_user_id: int
    reason: Optional[str] = None


async def _campaign(db: AsyncSession, campaign_id: int) -> AdOutreachCampaign:
    row = await db.get(AdOutreachCampaign, int(campaign_id))
    if row is None:
        raise HTTPException(status_code=404, detail="campaign not found")
    return row


@router.get("/campaigns")
async def list_campaigns(db: AsyncSession = Depends(get_db_session)):
    rows = (
        (await db.execute(select(AdOutreachCampaign).order_by(AdOutreachCampaign.id.desc())))
        .scalars()
        .all()
    )
    out = []
    for c in rows:
        d = c.to_dict()
        d["counters"] = await outreach.campaign_counters(db, c.id)
        out.append(d)
    return {"campaigns": out, "disabled": outreach.outreach_disabled()}


@router.post("/campaigns")
async def create_campaign(payload: CampaignIn, db: AsyncSession = Depends(get_db_session)):
    row = AdOutreachCampaign(
        title=payload.title.strip(),
        template_id=payload.template_id,
        months_back=payload.months_back,
        per_community_daily=payload.per_community_daily,
        total_daily=payload.total_daily,
        quiet_start=payload.quiet_start,
        quiet_end=payload.quiet_end,
        images_json=[str(x) for x in payload.images][:5],
        note=payload.note,
        status="draft",
        dry_run=True,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row.to_dict()


@router.get("/campaigns/{campaign_id}")
async def get_campaign(campaign_id: int, db: AsyncSession = Depends(get_db_session)):
    c = await _campaign(db, campaign_id)
    d = c.to_dict()
    d["counters"] = await outreach.campaign_counters(db, c.id)
    d["template_body"] = await outreach.resolve_template(db, c)
    return d


@router.post("/campaigns/{campaign_id}/enroll")
async def enroll(campaign_id: int, db: AsyncSession = Depends(get_db_session)):
    c = await _campaign(db, campaign_id)
    if c.status == "stopped":
        raise HTTPException(status_code=409, detail="кампания остановлена")
    stats = await outreach.enroll_campaign(db, c)
    if c.status == "done" and stats.get("auto"):
        c.status = "running"  # добор новых адресатов возвращает кампанию в работу
        c.finished_at = None
    await db.commit()
    return {"campaign_id": c.id, **stats, "counters": await outreach.campaign_counters(db, c.id)}


@router.post("/campaigns/{campaign_id}/start")
async def start(campaign_id: int, payload: StartIn, db: AsyncSession = Depends(get_db_session)):
    c = await _campaign(db, campaign_id)
    if c.status in ("done", "stopped"):
        raise HTTPException(status_code=409, detail="кампания завершена")
    if not await outreach.resolve_template(db, c):
        raise HTTPException(status_code=400, detail="нет шаблона категории ad_offer")
    c.dry_run = bool(payload.dry_run)
    c.status = "running"
    c.paused_until = None
    c.paused_reason = None
    c.started_at = c.started_at or datetime.utcnow()
    if not payload.dry_run:
        # Сухие строки возвращаем в очередь: боевой запуск шлёт их по-настоящему.
        rows = (
            (
                await db.execute(
                    select(AdOutreachRecipient).where(
                        AdOutreachRecipient.campaign_id == c.id,
                        AdOutreachRecipient.status == "dry_run",
                    )
                )
            )
            .scalars()
            .all()
        )
        for r in rows:
            r.status = "pending"
    await db.commit()
    await db.refresh(c)
    return c.to_dict()


@router.post("/campaigns/{campaign_id}/pause")
async def pause(campaign_id: int, db: AsyncSession = Depends(get_db_session)):
    c = await _campaign(db, campaign_id)
    c.status = "paused"
    await db.commit()
    return c.to_dict()


@router.post("/campaigns/{campaign_id}/stop")
async def stop(campaign_id: int, db: AsyncSession = Depends(get_db_session)):
    c = await _campaign(db, campaign_id)
    c.status = "stopped"
    c.finished_at = datetime.utcnow()
    await db.commit()
    return c.to_dict()


@router.post("/campaigns/{campaign_id}/dispatch")
async def dispatch_now(campaign_id: int, db: AsyncSession = Depends(get_db_session)):
    """Тик руками — тот же код, что у beat (полезно для пилота на 10 адресатах)."""
    c = await _campaign(db, campaign_id)
    if c.status != "running":
        raise HTTPException(status_code=409, detail="кампания не запущена")
    return await outreach.run_outreach_tick(campaign_id=c.id)


@router.get("/campaigns/{campaign_id}/preview")
async def preview(campaign_id: int, db: AsyncSession = Depends(get_db_session)):
    """Ровно тот текст, что уйдёт первому адресату очереди (до запуска)."""
    c = await _campaign(db, campaign_id)
    template = await outreach.resolve_template(db, c)
    if not template:
        raise HTTPException(status_code=400, detail="нет шаблона категории ad_offer")
    r = (
        await db.execute(
            select(AdOutreachRecipient)
            .where(AdOutreachRecipient.campaign_id == c.id)
            .order_by(AdOutreachRecipient.id.asc())
            .limit(1)
        )
    ).scalar_one_or_none()
    ctx = await outreach._render_context(db, r, {}) if r is not None else {}
    body = outreach.render_offer(
        template,
        author_name=r.name if r is not None else "Имя",
        cabinet_id=r.client_id if r is not None else None,
        **ctx,
    )
    return {"recipient": r.to_dict() if r is not None else None, "body": body, "length": len(body)}


@router.get("/campaigns/{campaign_id}/recipients")
async def recipients(
    campaign_id: int,
    status: Optional[str] = None,
    mode: Optional[str] = None,
    limit: int = 500,
    db: AsyncSession = Depends(get_db_session),
):
    await _campaign(db, campaign_id)
    stmt = select(AdOutreachRecipient).where(AdOutreachRecipient.campaign_id == campaign_id)
    if status:
        stmt = stmt.where(AdOutreachRecipient.status == status)
    if mode:
        stmt = stmt.where(AdOutreachRecipient.mode == mode)
    rows = (
        (
            await db.execute(
                stmt.order_by(AdOutreachRecipient.id.asc()).limit(max(1, min(limit, 2000)))
            )
        )
        .scalars()
        .all()
    )
    return {"recipients": [r.to_dict() for r in rows]}


@router.get("/campaigns/{campaign_id}/manual")
async def manual(campaign_id: int, db: AsyncSession = Depends(get_db_session)):
    await _campaign(db, campaign_id)
    return {"recipients": await outreach.manual_list(db, campaign_id)}


@router.post("/recipients/{recipient_id}/done")
async def manual_done(recipient_id: int, db: AsyncSession = Depends(get_db_session)):
    r = await db.get(AdOutreachRecipient, int(recipient_id))
    if r is None:
        raise HTTPException(status_code=404, detail="recipient not found")
    r.status = "done_manual"
    r.sent_at = datetime.utcnow()
    await db.commit()
    return r.to_dict()


@router.post("/blacklist")
async def blacklist_add(payload: BlacklistIn, db: AsyncSession = Depends(get_db_session)):
    row = await db.get(AdOutreachBlacklist, int(payload.vk_user_id))
    if row is None:
        row = AdOutreachBlacklist(vk_user_id=int(payload.vk_user_id), reason=payload.reason)
        db.add(row)
    else:
        row.reason = payload.reason or row.reason
        row.until = None
    # Уже набранного в кампании — снимаем с очереди.
    rows = (
        (
            await db.execute(
                select(AdOutreachRecipient).where(
                    AdOutreachRecipient.vk_user_id == int(payload.vk_user_id),
                    AdOutreachRecipient.status.in_(("pending", "manual", "dry_run")),
                )
            )
        )
        .scalars()
        .all()
    )
    for r in rows:
        r.status = "skipped"
        r.error = "стоп-лист"
    await db.commit()
    return {"blacklisted": row.to_dict(), "skipped": len(rows)}
