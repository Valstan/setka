"""API сетевой рассылки (`/api/broadcast`).

Внутренний планировщик-публикатор (директива brain 2026-06-14): кампания =
текст+медиа + набор целей + расписание/повтор. Публикует свой беат-диспетчер
(``modules/broadcast/dispatcher.py``) через ``wall.post`` немедленно — НЕ в
VK-отложку. Всё управление (правка текста/целей/расписания/очереди) — здесь.

Эндпоинты:
- ``GET    /campaigns``                 — список кампаний (со сводкой публикаций);
- ``POST   /campaigns``                 — создать (черновик; цели по умолч. = все);
- ``GET    /campaigns/{id}``            — детали + цели + per-target публикации;
- ``PUT    /campaigns/{id}``            — править (пока не done/cancelled);
- ``POST   /campaigns/{id}/schedule``   — запланировать (status=scheduled);
- ``POST   /campaigns/{id}/cancel``     — отменить;
- ``POST   /campaigns/{id}/retry``      — перепослать ошибки последнего прогона;
- ``DELETE /campaigns/{id}``            — удалить;
- ``GET    /default-targets``           — паблики сети по умолчанию (для UI);
- ``GET/POST/DELETE /images``           — библиотека картинок кампаний.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.connection import get_db_session
from database.models import BroadcastCampaign, BroadcastPublication, BroadcastTarget
from modules.broadcast.service import (
    MAX_IMG_BYTES,
    broadcast_image_dir,
    default_targets,
    safe_image_name,
)

logger = logging.getLogger(__name__)
router = APIRouter()

MSK = timezone(timedelta(hours=3))

# Минимальный интервал между повторами (часы): для repeat_count>1 защищает от
# «машинганнинга» сети — interval=0 иначе перепубликовывал бы каждый тик беата.
# 0.25ч (15 мин) комфортно больше длительности прогона (16 целей @5с ≈ 80с).
MIN_REPEAT_INTERVAL_HOURS = 0.25


def _clamp_interval(interval: float, repeat_count: int) -> float:
    interval = max(0.0, float(interval or 0))
    if repeat_count > 1:
        return max(interval, MIN_REPEAT_INTERVAL_HOURS)
    return interval


class CampaignIn(BaseModel):
    title: str = ""
    body: str = ""
    image_names: Optional[List[str]] = None
    target_group_ids: Optional[List[int]] = None  # None/[] → все паблики сети
    scheduled_at: Optional[str] = None  # ISO datetime по МСК
    repeat_count: int = 1
    repeat_interval_hours: Optional[float] = None
    vary_per_target: bool = False


class ScheduleIn(BaseModel):
    scheduled_at: Optional[str] = None  # если не передан — берём с кампании


def _parse_msk(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Некорректная дата: {value}")
    if dt.tzinfo is not None:
        dt = dt.astimezone(MSK).replace(tzinfo=None)
    return dt


async def _resolve_targets(session: AsyncSession, group_ids: Optional[List[int]]) -> List[dict]:
    """Список целей: явный набор group_id → их имена из regions; иначе все паблики."""
    if not group_ids:
        return await default_targets(session)
    from database.models import Region

    rows = (
        await session.execute(
            select(Region.vk_group_id, Region.name).where(Region.vk_group_id.in_(group_ids))
        )
    ).all()
    name_by_gid = {int(gid): (name or "") for gid, name in rows if gid is not None}
    return [
        {"group_id": int(g), "name": name_by_gid.get(int(g), "")} for g in dict.fromkeys(group_ids)
    ]


async def _set_targets(session: AsyncSession, campaign_id: int, targets: List[dict]) -> None:
    await session.execute(delete(BroadcastTarget).where(BroadcastTarget.campaign_id == campaign_id))
    for t in targets:
        session.add(
            BroadcastTarget(
                campaign_id=campaign_id, group_id=int(t["group_id"]), name=t.get("name")
            )
        )


async def _load(session: AsyncSession, campaign_id: int) -> BroadcastCampaign:
    camp = (
        await session.execute(select(BroadcastCampaign).where(BroadcastCampaign.id == campaign_id))
    ).scalar_one_or_none()
    if camp is None:
        raise HTTPException(status_code=404, detail="Кампания не найдена")
    return camp


# ----------------------------------------------------------------------
# Кампании
# ----------------------------------------------------------------------


@router.get("/campaigns")
async def list_campaigns(session: AsyncSession = Depends(get_db_session)):
    """Список кампаний (свежие сверху) со сводкой целей и публикаций."""
    camps = (
        (await session.execute(select(BroadcastCampaign).order_by(BroadcastCampaign.id.desc())))
        .scalars()
        .all()
    )
    out = []
    for c in camps:
        targets = (
            (
                await session.execute(
                    select(BroadcastTarget).where(BroadcastTarget.campaign_id == c.id)
                )
            )
            .scalars()
            .all()
        )
        pubs = (
            (
                await session.execute(
                    select(BroadcastPublication).where(BroadcastPublication.campaign_id == c.id)
                )
            )
            .scalars()
            .all()
        )
        d = c.to_dict()
        d["targets_count"] = len(targets)
        d["published_count"] = sum(1 for p in pubs if p.status == "published")
        d["error_count"] = sum(1 for p in pubs if p.status == "error")
        out.append(d)
    return {"campaigns": out}


@router.post("/campaigns")
async def create_campaign(payload: CampaignIn, session: AsyncSession = Depends(get_db_session)):
    """Создать кампанию (черновик). Цели по умолчанию = все паблики сети."""
    repeat_count = max(1, int(payload.repeat_count or 1))
    interval = payload.repeat_interval_hours
    if interval is None:
        from config.runtime import get_broadcast_default_repeat_interval_hours

        interval = get_broadcast_default_repeat_interval_hours()
    interval = _clamp_interval(interval, repeat_count)
    camp = BroadcastCampaign(
        title=(payload.title or "").strip(),
        body=payload.body or "",
        image_names=payload.image_names or [],
        attachments=None,
        status="draft",
        scheduled_at=_parse_msk(payload.scheduled_at),
        repeat_count=repeat_count,
        repeat_interval_hours=interval,
        runs_done=0,
        vary_per_target=bool(payload.vary_per_target),
    )
    session.add(camp)
    await session.flush()  # получить id
    targets = await _resolve_targets(session, payload.target_group_ids)
    await _set_targets(session, camp.id, targets)
    await session.commit()
    await session.refresh(camp)
    return {"success": True, "campaign": camp.to_dict(), "targets_count": len(targets)}


@router.get("/campaigns/{campaign_id}")
async def get_campaign(campaign_id: int, session: AsyncSession = Depends(get_db_session)):
    """Детали кампании + цели + per-target публикации (последние прогоны сверху)."""
    camp = await _load(session, campaign_id)
    targets = (
        (
            await session.execute(
                select(BroadcastTarget).where(BroadcastTarget.campaign_id == camp.id)
            )
        )
        .scalars()
        .all()
    )
    pubs = (
        (
            await session.execute(
                select(BroadcastPublication)
                .where(BroadcastPublication.campaign_id == camp.id)
                .order_by(BroadcastPublication.run_index.desc(), BroadcastPublication.id.desc())
            )
        )
        .scalars()
        .all()
    )
    return {"campaign": camp.to_dict(targets=targets, publications=pubs)}


@router.put("/campaigns/{campaign_id}")
async def update_campaign(
    campaign_id: int, payload: CampaignIn, session: AsyncSession = Depends(get_db_session)
):
    """Править кампанию (текст/медиа/цели/расписание). Запрещено для done/cancelled."""
    camp = await _load(session, campaign_id)
    if camp.status in ("done", "cancelled"):
        raise HTTPException(
            status_code=409, detail=f"Кампанию нельзя править в статусе {camp.status}"
        )

    camp.title = (payload.title or "").strip()
    new_body = payload.body or ""
    new_images = payload.image_names or []
    # Сменились текст/картинки → сбросить кэш attachment'ов (зальём заново).
    if new_images != (camp.image_names or []):
        camp.attachments = None
    camp.body = new_body
    camp.image_names = new_images
    camp.repeat_count = max(1, int(payload.repeat_count or 1))
    if payload.repeat_interval_hours is not None:
        camp.repeat_interval_hours = float(payload.repeat_interval_hours)
    camp.repeat_interval_hours = _clamp_interval(camp.repeat_interval_hours, camp.repeat_count)
    camp.vary_per_target = bool(payload.vary_per_target)
    if payload.scheduled_at is not None:
        camp.scheduled_at = _parse_msk(payload.scheduled_at)
        if camp.status == "scheduled":
            camp.next_run_at = camp.scheduled_at

    if payload.target_group_ids is not None:
        targets = await _resolve_targets(session, payload.target_group_ids)
        await _set_targets(session, camp.id, targets)
    await session.commit()
    await session.refresh(camp)
    return {"success": True, "campaign": camp.to_dict()}


@router.post("/campaigns/{campaign_id}/schedule")
async def schedule_campaign(
    campaign_id: int, payload: ScheduleIn, session: AsyncSession = Depends(get_db_session)
):
    """Запланировать кампанию: status=scheduled, next_run_at = время первого прогона."""
    camp = await _load(session, campaign_id)
    if camp.status in ("done", "cancelled"):
        raise HTTPException(status_code=409, detail=f"Кампания в статусе {camp.status}")
    when = _parse_msk(payload.scheduled_at) if payload.scheduled_at else camp.scheduled_at
    if when is None:
        raise HTTPException(status_code=400, detail="Не задано время публикации (scheduled_at)")
    targets_count = (
        (
            await session.execute(
                select(BroadcastTarget).where(BroadcastTarget.campaign_id == camp.id)
            )
        )
        .scalars()
        .all()
    )
    if not targets_count:
        raise HTTPException(status_code=400, detail="У кампании нет целей")
    camp.scheduled_at = when
    camp.next_run_at = when
    camp.runs_done = 0
    camp.status = "scheduled"
    await session.commit()
    await session.refresh(camp)
    return {"success": True, "campaign": camp.to_dict()}


@router.post("/campaigns/{campaign_id}/cancel")
async def cancel_campaign(campaign_id: int, session: AsyncSession = Depends(get_db_session)):
    """Отменить кампанию (диспетчер её больше не возьмёт)."""
    camp = await _load(session, campaign_id)
    camp.status = "cancelled"
    await session.commit()
    return {"success": True}


@router.post("/campaigns/{campaign_id}/retry")
async def retry_campaign(campaign_id: int, session: AsyncSession = Depends(get_db_session)):
    """Перепослать ошибки/незавершённое последнего прогона.

    Удаляет error/pending-публикации последнего прогона и снова ставит кампанию
    в очередь (next_run_at=now). Успешные публикации остаются (не дублируем)."""
    camp = await _load(session, campaign_id)
    if camp.status == "cancelled":
        raise HTTPException(status_code=409, detail="Кампания отменена")
    last_run = (
        camp.runs_done - 1 if camp.status == "done" and camp.runs_done > 0 else camp.runs_done
    )
    deleted = await session.execute(
        delete(BroadcastPublication).where(
            BroadcastPublication.campaign_id == camp.id,
            BroadcastPublication.run_index == last_run,
            BroadcastPublication.status.in_(("error", "pending")),
        )
    )
    cleared = deleted.rowcount or 0
    # Нечего повторять (нет error/pending в последнем прогоне) → не перематываем
    # завершённую кампанию обратно в очередь (иначе done молча оживает зря).
    if cleared == 0 and camp.status == "done":
        return {"success": True, "retried_run": last_run, "cleared": 0, "note": "нечего повторять"}
    camp.runs_done = last_run
    camp.status = "scheduled"
    camp.next_run_at = datetime.now(MSK).replace(tzinfo=None)
    await session.commit()
    return {"success": True, "retried_run": last_run, "cleared": cleared}


@router.delete("/campaigns/{campaign_id}")
async def delete_campaign(campaign_id: int, session: AsyncSession = Depends(get_db_session)):
    """Удалить кампанию (вместе с целями и публикациями, CASCADE)."""
    camp = await _load(session, campaign_id)
    await session.delete(camp)
    await session.commit()
    return {"success": True}


@router.get("/default-targets")
async def get_default_targets(session: AsyncSession = Depends(get_db_session)):
    """Паблики сети по умолчанию (активные регионы с vk_group_id) — для UI."""
    return {"targets": await default_targets(session)}


# ----------------------------------------------------------------------
# Библиотека картинок кампаний (CRUD)
# ----------------------------------------------------------------------


def _image_dto(p) -> dict:
    from urllib.parse import quote

    return {"name": p.name, "url": "/static/broadcast/" + quote(p.name), "size": p.stat().st_size}


@router.get("/images")
async def list_images():
    """Список загруженных картинок кампаний с превью-URL."""
    d = broadcast_image_dir()
    paths = sorted(
        p for p in d.iterdir() if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg", ".png")
    )
    return {"images": [_image_dto(p) for p in paths]}


@router.post("/images")
async def upload_image(file: UploadFile = File(...)):
    """Загрузить картинку для кампаний (JPG/PNG, до 12 МБ)."""
    try:
        name = safe_image_name(file.filename or "post.png")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Пустой файл")
    if len(data) > MAX_IMG_BYTES:
        raise HTTPException(
            status_code=400, detail=f"Файл больше {MAX_IMG_BYTES // (1024 * 1024)} МБ"
        )
    dest = broadcast_image_dir() / name
    dest.write_bytes(data)
    return _image_dto(dest)


@router.delete("/images/{name}")
async def delete_image(name: str):
    """Удалить картинку из библиотеки кампаний."""
    try:
        safe = safe_image_name(name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    p = broadcast_image_dir() / safe
    if not p.is_file():
        raise HTTPException(status_code=404, detail="Файл не найден")
    p.unlink()
    return {"success": True}
