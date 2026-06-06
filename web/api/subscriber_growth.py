"""Сравнительная динамика роста подписчиков сообществ (`/api/subscriber-growth`).

Owner-request 2026-06-05 / brain-директива 2026-06-06 (#027-сосед, recommend):
видеть, **кто из сообществ как растёт, и сравнивать между собой** — один график,
много серий, чекбоксы-переключатели под ним; отстающих выделять для ручной
раскрутки.

Фундамент — `community_member_snapshots` (миграция 031): дневные снимки
`members_count` по каждому активному сообществу (beat `collect-member-snapshots-daily`).
Кривая строится из накопленных точек; задним числом историю не достать.

Метрика — **только подписчики**. Просмотры/охват (`stats.get`) probe-gated и по
живому VK-probe 2026-06-06 (`scripts/probe_stats_get_capability.py`) доступны лишь
**админ-токену своих групп** (community-токен → VK error 27, чужие группы → 15),
т.е. не как сравнение по всем отслеживаемым сообществам. Подписчики
(`groups.getById fields=members_count`) — публичны и единственная универсальная метрика.

Эндпоинты:
  * ``GET /communities`` — сообщества со снимками + сводка роста для чекбоксов;
  * ``GET /series?ids=1,2,3&days=90`` — единая ось дат + ряд на каждое выбранное сообщество.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.connection import get_db_session
from database.models import Community, CommunityMemberSnapshot, Region

logger = logging.getLogger(__name__)
router = APIRouter()

_MAX_DAYS = 365
_DEFAULT_DAYS = 90
# Максимум серий на графике за раз — защита и от тяжёлого ответа, и от каши на canvas.
_MAX_SERIES = 30


def _day_key(d: Any) -> str:
    """snapshot_date (date|datetime|str) → 'YYYY-MM-DD'."""
    return d.isoformat() if hasattr(d, "isoformat") else str(d)


def summarize_communities(
    rows: Iterable[Tuple[int, Any, int]],
    meta_by_id: Dict[int, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Свод по сообществам из снимков (`(community_id, snapshot_date, members_count)`).

    ``rows`` должны быть **отсортированы** по ``(community_id, snapshot_date)``.
    Возвращает по строке на сообщество: latest/first count, абсолютная и
    процентная дельта за окно, число точек и флаг ``is_laggard``.

    Отстающий = есть ≥2 точек И рост ``delta <= 0`` (плоский/отрицательный). Порог
    «ниже медианы» не зашиваем вслепую (директива R3) — это считает анализатор
    позже на реальных данных; здесь — только явный нулевой/отрицательный рост.
    """
    agg: Dict[int, Dict[str, Any]] = {}
    for community_id, snap_date, count in rows:
        cid = int(community_id)
        cnt = int(count)
        bucket = agg.get(cid)
        if bucket is None:
            agg[cid] = {
                "first": cnt,
                "first_date": _day_key(snap_date),
                "latest": cnt,
                "latest_date": _day_key(snap_date),
                "points": 1,
            }
        else:
            bucket["latest"] = cnt
            bucket["latest_date"] = _day_key(snap_date)
            bucket["points"] += 1

    out: List[Dict[str, Any]] = []
    for cid, b in agg.items():
        meta = meta_by_id.get(cid, {})
        delta = b["latest"] - b["first"]
        delta_pct = round(delta / b["first"] * 100, 2) if b["first"] else 0.0
        out.append(
            {
                "id": cid,
                "name": meta.get("name") or f"community {cid}",
                "category": meta.get("category"),
                "region": meta.get("region"),
                "latest_count": b["latest"],
                "first_count": b["first"],
                "delta": delta,
                "delta_pct": delta_pct,
                "points": b["points"],
                "first_date": b["first_date"],
                "latest_date": b["latest_date"],
                "is_laggard": b["points"] >= 2 and delta <= 0,
            }
        )
    # Быстрее растущие сверху; при равенстве — по имени для стабильности.
    out.sort(key=lambda r: (-r["delta"], (r["name"] or "").lower()))
    return out


def build_series(
    rows: Iterable[Tuple[int, Any, int]],
    names_by_id: Dict[int, str],
) -> Dict[str, Any]:
    """Снимки → единая ось дат + ряд на сообщество для мульти-line Chart.js.

    ``rows`` — ``(community_id, snapshot_date, members_count)``. Ось X — объединение
    всех дат выбранных сообществ (отсортированы); пропуски = ``None`` (Chart.js
    рисует разрыв). Так кривые разной длины сравнимы на одном графике.
    """
    by_comm: Dict[int, Dict[str, int]] = {}
    all_days: set = set()
    for community_id, snap_date, count in rows:
        cid = int(community_id)
        day = _day_key(snap_date)
        all_days.add(day)
        by_comm.setdefault(cid, {})[day] = int(count)

    labels = sorted(all_days)
    series: List[Dict[str, Any]] = []
    for cid, day_map in by_comm.items():
        series.append(
            {
                "id": cid,
                "name": names_by_id.get(cid) or f"community {cid}",
                "data": [day_map.get(day) for day in labels],
            }
        )
    series.sort(key=lambda s: (s["name"] or "").lower())
    return {"labels": labels, "series": series}


def _window_start(days: int) -> datetime:
    days = max(1, min(int(days), _MAX_DAYS))
    return (datetime.utcnow() - timedelta(days=days - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


def _parse_ids(ids: Optional[str]) -> List[int]:
    if not ids:
        return []
    out: List[int] = []
    for part in ids.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(int(part))
        except ValueError:
            continue
    return out


@router.get("/communities")
async def list_growth_communities(
    days: int = Query(_DEFAULT_DAYS, ge=1, le=_MAX_DAYS),
    db: AsyncSession = Depends(get_db_session),
):
    """Сообщества, по которым есть снимки за окно, + сводка роста для панели чекбоксов."""
    start = _window_start(days).date()

    snap_rows = (
        await db.execute(
            select(
                CommunityMemberSnapshot.community_id,
                CommunityMemberSnapshot.snapshot_date,
                CommunityMemberSnapshot.members_count,
            )
            .where(CommunityMemberSnapshot.snapshot_date >= start)
            .order_by(
                CommunityMemberSnapshot.community_id,
                CommunityMemberSnapshot.snapshot_date,
            )
        )
    ).all()

    ids = sorted({int(r[0]) for r in snap_rows})
    meta_by_id: Dict[int, Dict[str, Any]] = {}
    if ids:
        meta_rows = (
            await db.execute(
                select(
                    Community.id,
                    Community.name,
                    Community.category,
                    Region.name,
                )
                .join(Region, Community.region_id == Region.id, isouter=True)
                .where(Community.id.in_(ids))
            )
        ).all()
        meta_by_id = {
            int(cid): {"name": name, "category": category, "region": region}
            for cid, name, category, region in meta_rows
        }

    communities = summarize_communities(snap_rows, meta_by_id)
    return {
        "days": days,
        "count": len(communities),
        "laggards": sum(1 for c in communities if c["is_laggard"]),
        "communities": communities,
    }


@router.get("/series")
async def growth_series(
    ids: Optional[str] = Query(None, description="CSV community ids, напр. 1,2,3"),
    days: int = Query(_DEFAULT_DAYS, ge=1, le=_MAX_DAYS),
    db: AsyncSession = Depends(get_db_session),
):
    """Мульти-серийный ряд подписчиков по выбранным сообществам (единая ось дат)."""
    start = _window_start(days).date()
    wanted = _parse_ids(ids)[:_MAX_SERIES]
    if not wanted:
        return {"days": days, "labels": [], "series": []}

    snap_rows = (
        await db.execute(
            select(
                CommunityMemberSnapshot.community_id,
                CommunityMemberSnapshot.snapshot_date,
                CommunityMemberSnapshot.members_count,
            )
            .where(
                CommunityMemberSnapshot.community_id.in_(wanted),
                CommunityMemberSnapshot.snapshot_date >= start,
            )
            .order_by(
                CommunityMemberSnapshot.community_id,
                CommunityMemberSnapshot.snapshot_date,
            )
        )
    ).all()

    names_rows = (
        await db.execute(select(Community.id, Community.name).where(Community.id.in_(wanted)))
    ).all()
    names_by_id = {int(cid): name for cid, name in names_rows}

    result = build_series(snap_rows, names_by_id)
    result["days"] = days
    return result
