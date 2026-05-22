"""Message templates CRUD API (etap 4b).

Powers the `/templates` page and the dropdown in the reply modal on
`/notifications`. Plain CRUD over `message_templates` table; no special
business logic — list endpoint returns only `is_active=True` for the UI
dropdown and the full set for the management page (via `?include_inactive=1`).
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from database.connection import AsyncSessionLocal
from database.models import MessageTemplate

logger = logging.getLogger(__name__)
router = APIRouter()


class TemplateIn(BaseModel):
    title: str = Field(..., min_length=1, max_length=120)
    body: str = Field(..., min_length=1)
    category: Optional[str] = Field(default=None, max_length=50)
    is_active: bool = True


class TemplateOut(BaseModel):
    id: int
    title: str
    body: str
    category: Optional[str]
    is_active: bool
    created_at: Optional[str]
    updated_at: Optional[str]


@router.get("/")
async def list_templates(include_inactive: bool = False):
    """List templates. By default only active ones are returned (UI dropdown
    use-case); pass `?include_inactive=1` for the management page."""
    async with AsyncSessionLocal() as session:
        stmt = select(MessageTemplate).order_by(
            MessageTemplate.category.is_(None), MessageTemplate.category, MessageTemplate.title
        )
        if not include_inactive:
            stmt = stmt.where(MessageTemplate.is_active.is_(True))
        rows = (await session.execute(stmt)).scalars().all()
        return {"templates": [r.to_dict() for r in rows]}


@router.post("/")
async def create_template(payload: TemplateIn):
    async with AsyncSessionLocal() as session:
        tpl = MessageTemplate(
            title=payload.title.strip(),
            body=payload.body.strip(),
            category=(payload.category or None),
            is_active=payload.is_active,
        )
        session.add(tpl)
        await session.commit()
        await session.refresh(tpl)
        return tpl.to_dict()


@router.put("/{template_id}")
async def update_template(template_id: int, payload: TemplateIn):
    async with AsyncSessionLocal() as session:
        tpl = await session.get(MessageTemplate, template_id)
        if not tpl:
            raise HTTPException(status_code=404, detail="template not found")
        tpl.title = payload.title.strip()
        tpl.body = payload.body.strip()
        tpl.category = payload.category or None
        tpl.is_active = payload.is_active
        await session.commit()
        await session.refresh(tpl)
        return tpl.to_dict()


@router.delete("/{template_id}")
async def delete_template(template_id: int):
    async with AsyncSessionLocal() as session:
        tpl = await session.get(MessageTemplate, template_id)
        if not tpl:
            raise HTTPException(status_code=404, detail="template not found")
        await session.delete(tpl)
        await session.commit()
        return {"success": True}
