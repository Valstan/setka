"""Сравнительная динамика роста подписчиков ГЛАВНЫХ ИНФО-групп регионов
(`/api/subscriber-growth`).

Owner-request 2026-06-05 / brain-директива 2026-06-06 (#027-сосед, recommend):
видеть, **как растут главные группы регионов и сравнивать между собой** — один
график, много серий, чекбоксы-переключатели под ним; отстающих выделять для
ручной раскрутки.

Учитываем **только главные ИНФО-группы регионов** (`regions.vk_group_id` — куда
выпускаем дайджесты), а не весь пул источников (owner 2026-06-07): снимать ~840
сообществ ежедневно жгло VK API ради групп, которые не сравниваем.

Фундамент — `region_member_snapshots` (миграция 033, заменила per-community 031):
дневные снимки `members_count` по каждому активному региону (beat
`collect-member-snapshots-daily`). Кривая строится из накопленных точек; задним
числом историю не достать.

Метрика — **только подписчики**. Просмотры/охват (`stats.get`) probe-gated и по
живому VK-probe 2026-06-06 (`scripts/probe_stats_get_capability.py`) доступны лишь
**админ-токену своих групп** (community-токен → VK error 27, чужие группы → 15).
Подписчики (`groups.getById fields=members_count`) — публичны и универсальны.

Эндпоинты:
  * ``GET /regions`` — регионы со снимками + сводка роста для чекбоксов;
  * ``GET /series?ids=1,2,3&days=90`` — единая ось дат + ряд на каждый выбранный регион.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.connection import get_db_session
from database.models import OblastUniqueMemberSnapshot, Region, RegionMemberSnapshot

logger = logging.getLogger(__name__)
router = APIRouter()

_MAX_DAYS = 365
_DEFAULT_DAYS = 90
# Максимум серий на графике за раз — защита и от тяжёлого ответа, и от каши на canvas.
_MAX_SERIES = 30


def _day_key(d: Any) -> str:
    """snapshot_date (date|datetime|str) → 'YYYY-MM-DD'."""
    return d.isoformat() if hasattr(d, "isoformat") else str(d)


_INFO_SUFFIX_RE = re.compile(r"\s*[-–—]?\s*ИНФО\s*$", re.IGNORECASE)


def _clean_oblast_name(name: Optional[str]) -> Optional[str]:
    """«КИРОВСКАЯ ОБЛАСТЬ - ИНФО» → «КИРОВСКАЯ ОБЛАСТЬ» (для заголовков/кнопок)."""
    if not name:
        return name
    return _INFO_SUFFIX_RE.sub("", str(name)).strip() or str(name)


def summarize_regions(
    rows: Iterable[Tuple[int, Any, int]],
    meta_by_id: Dict[int, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Свод по регионам из снимков (`(region_id, snapshot_date, members_count)`).

    ``rows`` должны быть **отсортированы** по ``(region_id, snapshot_date)``.
    Возвращает по строке на регион: latest/first count, абсолютная и процентная
    дельта за окно, число точек и флаг ``is_laggard``.

    Отстающий = есть ≥2 точек И рост ``delta <= 0`` (плоский/отрицательный). Порог
    «ниже медианы» не зашиваем вслепую (директива R3) — это считает анализатор
    позже на реальных данных; здесь — только явный нулевой/отрицательный рост.
    """
    agg: Dict[int, Dict[str, Any]] = {}
    for region_id, snap_date, count in rows:
        rid = int(region_id)
        cnt = int(count)
        bucket = agg.get(rid)
        if bucket is None:
            agg[rid] = {
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
    for rid, b in agg.items():
        meta = meta_by_id.get(rid, {})
        delta = b["latest"] - b["first"]
        delta_pct = round(delta / b["first"] * 100, 2) if b["first"] else 0.0
        out.append(
            {
                "id": rid,
                "name": meta.get("name") or f"регион {rid}",
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
    """Снимки → единая ось дат + ряд на регион для мульти-line Chart.js.

    ``rows`` — ``(region_id, snapshot_date, members_count)``. Ось X — объединение
    всех дат выбранных регионов (отсортированы); пропуски = ``None`` (Chart.js
    рисует разрыв). Так кривые разной длины сравнимы на одном графике.
    """
    by_region: Dict[int, Dict[str, int]] = {}
    all_days: set = set()
    for region_id, snap_date, count in rows:
        rid = int(region_id)
        day = _day_key(snap_date)
        all_days.add(day)
        by_region.setdefault(rid, {})[day] = int(count)

    labels = sorted(all_days)
    series: List[Dict[str, Any]] = []
    for rid, day_map in by_region.items():
        series.append(
            {
                "id": rid,
                "name": names_by_id.get(rid) or f"регион {rid}",
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


@router.get("/regions")
async def list_growth_regions(
    days: int = Query(_DEFAULT_DAYS, ge=1, le=_MAX_DAYS),
    db: AsyncSession = Depends(get_db_session),
):
    """Регионы со снимками за окно + сводка роста + группировка по областям.

    Каждый регион получает ``oblast_id``/``oblast_name`` (район → его область,
    область → она сама) — фронт группирует список (Кировская/Татарстан отдельно)
    и сортирует внутри группы по числу подписчиков. Плюс top-level ``oblasts`` со
    сводкой для кнопок-агрегатов: ``latest_sum`` (районы + областная группа) и
    ``latest_unique`` (последний дедуп-снимок, ``null`` пока не считался).
    """
    start = _window_start(days).date()

    snap_rows = (
        await db.execute(
            select(
                RegionMemberSnapshot.region_id,
                RegionMemberSnapshot.snapshot_date,
                RegionMemberSnapshot.members_count,
            )
            .where(RegionMemberSnapshot.snapshot_date >= start)
            .order_by(
                RegionMemberSnapshot.region_id,
                RegionMemberSnapshot.snapshot_date,
            )
        )
    ).all()

    ids = sorted({int(r[0]) for r in snap_rows})
    meta_by_id: Dict[int, Dict[str, Any]] = {}
    latest_unique: Dict[int, int] = {}
    if ids:
        meta_rows = (
            await db.execute(
                select(
                    Region.id,
                    Region.name,
                    Region.kind,
                    Region.parent_region_id,
                ).where(Region.id.in_(ids))
            )
        ).all()
        meta_by_id = {int(r[0]): {"name": r[1], "kind": r[2], "parent": r[3]} for r in meta_rows}
        uniq_rows = (
            await db.execute(
                select(
                    OblastUniqueMemberSnapshot.oblast_region_id,
                    OblastUniqueMemberSnapshot.unique_count,
                    OblastUniqueMemberSnapshot.snapshot_date,
                )
                .where(OblastUniqueMemberSnapshot.snapshot_date >= start)
                .order_by(
                    OblastUniqueMemberSnapshot.oblast_region_id,
                    OblastUniqueMemberSnapshot.snapshot_date,
                )
            )
        ).all()
        for ob, uc, _d in uniq_rows:  # asc by date → last write wins = latest
            latest_unique[int(ob)] = int(uc)

    regions = summarize_regions(snap_rows, meta_by_id)

    # Привязать каждый регион к его области (район → parent; область → сама).
    for r in regions:
        m = meta_by_id.get(r["id"], {})
        ob_id = r["id"] if m.get("kind") == "oblast" else m.get("parent")
        r["oblast_id"] = int(ob_id) if ob_id is not None else None
        r["oblast_name"] = (
            _clean_oblast_name(meta_by_id.get(r["oblast_id"], {}).get("name"))
            if r["oblast_id"] in meta_by_id
            else None
        )

    # Свод по областям для кнопок-агрегатов: Σ (районы + областная группа) + дедуп.
    oblasts_map: Dict[int, Dict[str, Any]] = {}
    for r in regions:
        ob = r.get("oblast_id")
        if ob is None:
            continue
        bucket = oblasts_map.setdefault(
            ob,
            {"id": ob, "name": r.get("oblast_name"), "region_count": 0, "latest_sum": 0},
        )
        bucket["region_count"] += 1
        bucket["latest_sum"] += int(r.get("latest_count") or 0)
    oblasts = []
    for ob, bucket in oblasts_map.items():
        bucket["latest_unique"] = latest_unique.get(ob)
        oblasts.append(bucket)
    oblasts.sort(key=lambda o: (-(o["latest_sum"] or 0), (o["name"] or "").lower()))

    return {
        "days": days,
        "count": len(regions),
        "laggards": sum(1 for r in regions if r["is_laggard"]),
        "regions": regions,
        "oblasts": oblasts,
    }


@router.get("/series")
async def growth_series(
    ids: Optional[str] = Query(None, description="CSV region ids, напр. 1,2,3"),
    oblast_sum: Optional[str] = Query(
        None, description="CSV oblast region ids → линия Σ область (с дублями)"
    ),
    oblast_uniq: Optional[str] = Query(
        None, description="CSV oblast region ids → линия область без дублей (дедуп)"
    ),
    days: int = Query(_DEFAULT_DAYS, ge=1, le=_MAX_DAYS),
    db: AsyncSession = Depends(get_db_session),
):
    """Ряды подписчиков на единой оси дат: регионы + агрегаты по областям.

    * ``ids`` — ряд на каждый регион (как раньше).
    * ``oblast_sum`` — синтетическая линия «Σ область» = сумма подписчиков всех
      главных групп области по датам (районы + областная группа; с дублями).
    * ``oblast_uniq`` — линия «область без дублей» из недельных дедуп-снимков
      (`oblast_unique_member_snapshots`); точек меньше (раз в неделю).

    Все ряды на общей оси дат (объединение); пропуски = ``None`` (Chart.js рвёт
    линию). У каждого ряда ``kind`` = ``region`` / ``oblast_sum`` / ``oblast_uniq``.
    """
    start = _window_start(days).date()
    region_ids = _parse_ids(ids)[:_MAX_SERIES]
    sum_obl = _parse_ids(oblast_sum)
    uniq_obl = _parse_ids(oblast_uniq)
    if not (region_ids or sum_obl or uniq_obl):
        return {"days": days, "labels": [], "series": []}

    # 1. Состав областей для Σ (область + её районы). Только при запросе суммы.
    oblast_members: Dict[int, List[int]] = {}
    if sum_obl:
        member_rows = (
            await db.execute(
                select(Region.id, Region.kind, Region.parent_region_id).where(
                    or_(
                        Region.id.in_(sum_obl),
                        Region.parent_region_id.in_(sum_obl),
                    )
                )
            )
        ).all()
        for rid, kind, parent in member_rows:
            ob = int(rid) if kind == "oblast" else (int(parent) if parent is not None else None)
            if ob in sum_obl:
                oblast_members.setdefault(ob, []).append(int(rid))

    # 2. Имена областей (для подписей Σ / без дублей).
    involved_oblasts = sorted(set(sum_obl) | set(uniq_obl))
    oblast_names: Dict[int, str] = {}
    if involved_oblasts:
        name_rows = (
            await db.execute(select(Region.id, Region.name).where(Region.id.in_(involved_oblasts)))
        ).all()
        oblast_names = {int(i): _clean_oblast_name(n) for i, n in name_rows}

    # 3. Снимки регионов (запрошенные регионы + все члены запрошенных областей).
    need_ids = set(region_ids)
    for mids in oblast_members.values():
        need_ids.update(mids)
    by_region: Dict[int, Dict[str, int]] = {}
    all_days: set = set()
    if need_ids:
        snap_rows = (
            await db.execute(
                select(
                    RegionMemberSnapshot.region_id,
                    RegionMemberSnapshot.snapshot_date,
                    RegionMemberSnapshot.members_count,
                )
                .where(
                    RegionMemberSnapshot.region_id.in_(need_ids),
                    RegionMemberSnapshot.snapshot_date >= start,
                )
                .order_by(
                    RegionMemberSnapshot.region_id,
                    RegionMemberSnapshot.snapshot_date,
                )
            )
        ).all()
        for rid, snap_date, count in snap_rows:
            day = _day_key(snap_date)
            all_days.add(day)
            by_region.setdefault(int(rid), {})[day] = int(count)

    # 4. Имена запрошенных регионов.
    region_names: Dict[int, str] = {}
    if region_ids:
        rn_rows = (
            await db.execute(select(Region.id, Region.name).where(Region.id.in_(region_ids)))
        ).all()
        region_names = {int(i): n for i, n in rn_rows}

    # 5. Дедуп-снимки (только при запросе линии без дублей).
    uniq_by_oblast: Dict[int, Dict[str, int]] = {}
    if uniq_obl:
        uniq_rows = (
            await db.execute(
                select(
                    OblastUniqueMemberSnapshot.oblast_region_id,
                    OblastUniqueMemberSnapshot.snapshot_date,
                    OblastUniqueMemberSnapshot.unique_count,
                )
                .where(
                    OblastUniqueMemberSnapshot.oblast_region_id.in_(uniq_obl),
                    OblastUniqueMemberSnapshot.snapshot_date >= start,
                )
                .order_by(
                    OblastUniqueMemberSnapshot.oblast_region_id,
                    OblastUniqueMemberSnapshot.snapshot_date,
                )
            )
        ).all()
        for ob, snap_date, uc in uniq_rows:
            day = _day_key(snap_date)
            all_days.add(day)
            uniq_by_oblast.setdefault(int(ob), {})[day] = int(uc)

    labels = sorted(all_days)
    series: List[Dict[str, Any]] = []

    for rid in region_ids:
        if rid not in by_region:
            continue
        day_map = by_region[rid]
        series.append(
            {
                "id": rid,
                "name": region_names.get(rid) or f"регион {rid}",
                "data": [day_map.get(day) for day in labels],
                "kind": "region",
            }
        )

    for ob in sum_obl:
        members = oblast_members.get(ob, [])
        data: List[Optional[int]] = []
        for day in labels:
            vals = [by_region[m][day] for m in members if m in by_region and day in by_region[m]]
            data.append(sum(vals) if vals else None)
        series.append(
            {
                "id": f"obl:{ob}:sum",
                "name": f"Σ {oblast_names.get(ob) or ('область ' + str(ob))}",
                "data": data,
                "kind": "oblast_sum",
            }
        )

    for ob in uniq_obl:
        day_map = uniq_by_oblast.get(ob, {})
        series.append(
            {
                "id": f"obl:{ob}:uniq",
                "name": f"{oblast_names.get(ob) or ('область ' + str(ob))} (без дублей)",
                "data": [day_map.get(day) for day in labels],
                "kind": "oblast_uniq",
            }
        )

    return {"days": days, "labels": labels, "series": series}
