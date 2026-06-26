"""Статистика использования VK-шлюза (``/api/gateway-stats``) — операторская.

Доступ — только оператор (путь НЕ в ``PUBLIC_PREFIXES``, его закрывает
``AuthGateMiddleware`` сессионной cookie). Это внутренняя админка, в отличие от
самого шлюза ``/api/gateway`` (тот публичный, со своей X-API-Key защитой).

Читает таблицу ``gateway_requests`` (миграция 049): сводка по проектам, дневной
таймлайн для графика, последние запросы с параметрами («что искали/спрашивали»).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Query
from sqlalchemy import case, func, select

from database import models  # noqa: F401 - конфигурация мапперов
from database.connection import AsyncSessionLocal
from database.models import GatewayRequest

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/summary")
async def gateway_stats_summary(days: int = Query(30, ge=1, le=365)):
    """Сводка по проектам за ``days`` дней: запросов, последний раз, успехи/ошибки."""
    cutoff = datetime.utcnow() - timedelta(days=days)
    ok_sum = func.sum(case((GatewayRequest.ok.is_(True), 1), else_=0))
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(
                    GatewayRequest.project,
                    func.count().label("total"),
                    func.max(GatewayRequest.created_at).label("last_used"),
                    ok_sum.label("ok_count"),
                )
                .where(GatewayRequest.created_at >= cutoff)
                .group_by(GatewayRequest.project)
                .order_by(func.count().desc())
            )
        ).all()

    projects = []
    grand_total = 0
    for project, total, last_used, ok_count in rows:
        total = int(total or 0)
        ok_count = int(ok_count or 0)
        grand_total += total
        projects.append(
            {
                "project": project or "—",
                "total": total,
                "ok": ok_count,
                "errors": total - ok_count,
                "last_used": last_used.isoformat() if last_used else None,
            }
        )
    return {"days": days, "total": grand_total, "projects": projects}


@router.get("/timeline")
async def gateway_stats_timeline(days: int = Query(30, ge=1, le=365)):
    """Запросов по дням за ``days`` дней (для графика)."""
    cutoff = datetime.utcnow() - timedelta(days=days)
    day = func.date(GatewayRequest.created_at)
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(day.label("day"), func.count().label("total"))
                .where(GatewayRequest.created_at >= cutoff)
                .group_by(day)
                .order_by(day)
            )
        ).all()
    points = [{"day": str(d), "total": int(t or 0)} for d, t in rows]
    return {"days": days, "points": points}


@router.get("/recent")
async def gateway_stats_recent(
    limit: int = Query(50, ge=1, le=500),
    project: str = Query("", description="фильтр по проекту (опционально)"),
):
    """Последние запросы с параметрами («что искали/спрашивали»)."""
    async with AsyncSessionLocal() as session:
        stmt = select(GatewayRequest).order_by(GatewayRequest.created_at.desc()).limit(limit)
        if project:
            stmt = stmt.where(GatewayRequest.project == project)
        rows = (await session.execute(stmt)).scalars().all()
    return {"items": [r.to_dict() for r in rows]}
