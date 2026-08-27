"""API раздела «Раскрутка» — продвижение молодых сообществ сети.

Кто сюда ходит: страница ``/promotion`` в операторской зоне. Доступ закрывает
``AuthGateMiddleware`` (secure by default) — ни одна ручка здесь не публичная и
в PUBLIC-списки гейта ничего не добавляется.

Откуда данные: ``promo_*`` таблицы (миграция 087), ``regions``,
``region_member_snapshots`` и ``vk_tokens``. Ни один эндпоинт не обращается к VK
и ничего не публикует — на этапе 0 модуль только показывает, что он видит и что
предложил бы сделать.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from config.promo import get_oblast_group_id, promo_disabled
from database import models  # noqa: F401 - конфигурация мапперов
from database.connection import get_db_session
from database.models import (
    PromoAction,
    PromoEnrollment,
    PromoOutreachCandidate,
    PromoSettings,
    Region,
    RegionMemberSnapshot,
    VKToken,
)
from modules.promotion.hashtags import DISTRICT_ADJECTIVES
from modules.promotion.pairing import DonorCandidate, TargetCandidate, plan_pairs
from modules.region_links import build_neighbor_graph, community_url, short_name

logger = logging.getLogger(__name__)

router = APIRouter()


class SettingsPut(BaseModel):
    """Частичное обновление настроек: ``None`` = «не трогать это поле»."""

    threshold_members: Optional[int] = None
    graduate_members: Optional[int] = None
    donor_min_members: Optional[int] = None
    max_per_donor_per_week: Optional[int] = None
    max_per_target_per_week: Optional[int] = None
    max_actions_per_day: Optional[int] = None
    quiet_hours_start: Optional[int] = None
    quiet_hours_end: Optional[int] = None
    second_hop_enabled: Optional[bool] = None
    oblast_fallback_enabled: Optional[bool] = None
    channels: Optional[Dict[str, Any]] = None


async def _settings_row(db: AsyncSession) -> PromoSettings:
    row = (await db.execute(select(PromoSettings).limit(1))).scalar_one_or_none()
    if row is None:
        row = PromoSettings(id=1)
        db.add(row)
        await db.commit()
        await db.refresh(row)
    return row


async def _members_map(db: AsyncSession, *, on_day: Optional[date] = None) -> Dict[int, int]:
    """``{region_id: подписчиков}`` на последний снимок не позже ``on_day``.

    Перенос вперёд, а не «строго за дату»: ночной сбор иногда пропускает день, и
    отсутствие строки за вчера — это дыра в измерении, а не обвал сети.
    """
    query = select(
        RegionMemberSnapshot.region_id.label("region_id"),
        func.max(RegionMemberSnapshot.snapshot_date).label("day"),
    ).group_by(RegionMemberSnapshot.region_id)
    if on_day is not None:
        query = query.where(RegionMemberSnapshot.snapshot_date <= on_day)
    latest = query.subquery()

    rows = await db.execute(
        select(RegionMemberSnapshot.region_id, RegionMemberSnapshot.members_count).join(
            latest,
            (RegionMemberSnapshot.region_id == latest.c.region_id)
            & (RegionMemberSnapshot.snapshot_date == latest.c.day),
        )
    )
    return {region_id: members for region_id, members in rows.all()}


async def _community_token_groups(db: AsyncSession) -> Dict[int, str]:
    """``{abs(group_id): тип сообщества по карточке токена}``.

    Тип (``group`` / ``page``) достаётся из снимка карточки, который делает
    валидация ключа. Он важен для чек-листа: публичная страница видна в колонке
    «Интересные страницы» профиля подписчика и её нельзя скрыть, то есть даёт
    пассивный показ друзьям. Перевод группы в страницу делается руками в VK —
    API такого метода не даёт, поэтому это пункт задач владельцу, а не канал.
    """
    rows = await db.execute(
        select(VKToken.community_id, VKToken.user_info).where(
            VKToken.community_id.isnot(None), VKToken.is_active.is_(True)
        )
    )
    out: Dict[int, str] = {}
    for community_id, user_info in rows.all():
        if community_id is None:
            continue
        vk_type = ""
        if isinstance(user_info, dict):
            vk_type = str(user_info.get("type") or "")
        out[abs(int(community_id))] = vk_type
    return out


def _screen_name(region: Region) -> Optional[str]:
    """Красивый адрес сообщества, если ночная таска его закэшировала."""
    config = getattr(region, "config", None)
    if isinstance(config, dict):
        value = config.get("screen_name")
        if isinstance(value, str) and value:
            return value
    return None


def _hygiene(region: Region, *, vk_type: str, has_token: bool) -> Dict[str, Any]:
    """Чек-лист находимости района. Ни одного обращения к VK — всё уже в БД.

    Замер 28.08 показал, что растут ровно те районы, у которых это заполнено:
    хэштеги были у 13 старых, и они же единственные с заметным приростом.
    """
    gaps: List[str] = []
    if not (region.local_hashtags or "").strip():
        gaps.append("хэштеги")
    if region.vk_city_id is None:
        gaps.append("город")
    if not (region.center_city or "").strip():
        gaps.append("райцентр")
    if not has_token:
        gaps.append("ключ сообщества")
    if vk_type == "group":
        gaps.append("группа вместо публичной страницы")

    return {
        "has_hashtags": bool((region.local_hashtags or "").strip()),
        "hashtags": region.local_hashtags or "",
        "hashtags_known": region.code in DISTRICT_ADJECTIVES,
        "has_city": region.vk_city_id is not None,
        "has_center": bool((region.center_city or "").strip()),
        "has_community_token": has_token,
        "vk_type": vk_type or "",
        "is_public_page": vk_type == "page",
        "gaps": gaps,
    }


@router.get("/overview")
async def get_overview(db: AsyncSession = Depends(get_db_session)):
    """Сводка для шапки раздела."""
    settings = await _settings_row(db)

    enrolled = (
        await db.execute(
            select(func.count(PromoEnrollment.id)).where(PromoEnrollment.status == "active")
        )
    ).scalar() or 0
    graduated = (
        await db.execute(
            select(func.count(PromoEnrollment.id)).where(PromoEnrollment.status == "graduated")
        )
    ).scalar() or 0

    week_ago = date.today() - timedelta(days=7)
    actions_week = (
        await db.execute(
            select(func.count(PromoAction.id)).where(PromoAction.planned_at >= week_ago)
        )
    ).scalar() or 0
    published_week = (
        await db.execute(
            select(func.count(PromoAction.id)).where(
                PromoAction.planned_at >= week_ago, PromoAction.status == "published"
            )
        )
    ).scalar() or 0
    calls_week = (
        await db.execute(
            select(func.coalesce(func.sum(PromoAction.api_calls), 0)).where(
                PromoAction.planned_at >= week_ago
            )
        )
    ).scalar() or 0
    outreach_ready = (
        await db.execute(
            select(func.count(PromoOutreachCandidate.id)).where(
                PromoOutreachCandidate.status == "new"
            )
        )
    ).scalar() or 0

    return {
        "module_enabled": not promo_disabled(),
        "enrolled": enrolled,
        "graduated": graduated,
        "actions_last_7d": actions_week,
        "published_last_7d": published_week,
        "api_calls_last_7d": int(calls_week),
        "outreach_candidates": outreach_ready,
        "settings": settings.to_dict(),
    }


@router.get("/enrollments")
async def list_enrollments(db: AsyncSession = Depends(get_db_session)):
    """Районы с подписчиками, приростом, статусом зачисления и чек-листом находимости."""
    regions = (
        (await db.execute(select(Region).where(Region.kind == "raion").order_by(Region.code)))
        .scalars()
        .all()
    )

    today = date.today()
    members_now = await _members_map(db)
    members_week = await _members_map(db, on_day=today - timedelta(days=7))
    members_month = await _members_map(db, on_day=today - timedelta(days=30))
    token_types = await _community_token_groups(db)

    enrollments = {
        row.region_id: row for row in (await db.execute(select(PromoEnrollment))).scalars().all()
    }

    items = []
    for region in regions:
        group_id = region.vk_group_id
        abs_gid = abs(int(group_id)) if group_id else None
        vk_type = token_types.get(abs_gid, "") if abs_gid else ""
        has_token = abs_gid in token_types if abs_gid else False

        now_value = members_now.get(region.id)
        enrollment = enrollments.get(region.id)

        items.append(
            {
                "region_id": region.id,
                "code": region.code,
                "name": short_name(region.name, region.center_city),
                "is_active": bool(region.is_active),
                "url": community_url(group_id, _screen_name(region)) if group_id else None,
                "members": now_value,
                "delta_7d": (
                    now_value - members_week[region.id]
                    if now_value is not None and region.id in members_week
                    else None
                ),
                "delta_30d": (
                    now_value - members_month[region.id]
                    if now_value is not None and region.id in members_month
                    else None
                ),
                "status": enrollment.status if enrollment else None,
                "cohort": enrollment.cohort if enrollment else None,
                "hygiene": _hygiene(region, vk_type=vk_type, has_token=has_token),
            }
        )

    return {"regions": items, "total": len(items)}


@router.post("/enrollments/sync")
async def sync_now(db: AsyncSession = Depends(get_db_session)):
    """Пересчитать состав раскрутки и дозаполнить хэштеги сейчас.

    Пишет только в свои таблицы и в ``regions.local_hashtags``; в VK не ходит.
    """
    from modules.promotion.enrollment_service import sync_enrollments

    try:
        return await sync_enrollments(db)
    except Exception as exc:  # noqa: BLE001 - показываем причину оператору
        logger.error("promo sync failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Не удалось пересчитать: {exc}")


@router.get("/plan")
async def get_plan(
    db: AsyncSession = Depends(get_db_session),
    max_pairs: int = Query(10, ge=1, le=50),
):
    """Какие пары «донор → цель» модуль предложил бы сейчас. Ничего не публикует.

    Отдельно возвращается список районов **без** сетевого донора: их одиннадцать,
    и молчаливый пропуск читался бы как «район в работе», хотя работать по нему
    сеть не может — остаются только находимость и ручной аутрич.
    """
    settings = await _settings_row(db)
    regions = (await db.execute(select(Region))).scalars().all()
    by_id = {r.id: r for r in regions}
    members = await _members_map(db)
    token_types = await _community_token_groups(db)

    active_enrolled = {
        row.region_id
        for row in (
            await db.execute(select(PromoEnrollment).where(PromoEnrollment.status == "active"))
        )
        .scalars()
        .all()
    }

    targets = []
    donors = []
    for region in regions:
        if region.kind != "raion" or not region.is_active or not region.vk_group_id:
            continue
        count = members.get(region.id)
        abs_gid = abs(int(region.vk_group_id))
        if region.id in active_enrolled:
            targets.append(
                TargetCandidate(
                    region_id=region.id,
                    code=region.code,
                    group_id=region.vk_group_id,
                    members=count,
                )
            )
        if count is not None and count >= int(settings.donor_min_members):
            donors.append(
                DonorCandidate(
                    region_id=region.id,
                    code=region.code,
                    group_id=region.vk_group_id,
                    members=count,
                    has_community_token=abs_gid in token_types,
                )
            )

    graph = build_neighbor_graph(regions)
    pairs, orphans = plan_pairs(
        targets,
        donors,
        graph,
        second_hop_enabled=bool(settings.second_hop_enabled),
        max_pairs=max_pairs,
    )

    def _name(region_id: int) -> str:
        region = by_id.get(region_id)
        return short_name(region.name, region.center_city) if region else str(region_id)

    return {
        "pairs": [
            {
                "donor_code": pair.donor.code,
                "donor_name": _name(pair.donor.region_id),
                "donor_members": pair.donor.members,
                "donor_has_token": pair.donor.has_community_token,
                "target_code": pair.target.code,
                "target_name": _name(pair.target.region_id),
                "target_members": pair.target.members,
                "hop": pair.hop,
            }
            for pair in pairs
        ],
        "orphans": [
            {
                "code": orphan.target.code,
                "name": _name(orphan.target.region_id),
                "members": orphan.target.members,
                "reason": orphan.reason,
            }
            for orphan in orphans
        ],
        "oblast_group_id": get_oblast_group_id(),
        "oblast_fallback_enabled": bool(settings.oblast_fallback_enabled),
    }


@router.get("/actions")
async def list_actions(
    db: AsyncSession = Depends(get_db_session),
    limit: int = Query(100, ge=1, le=300),
    channel: Optional[str] = None,
    status: Optional[str] = None,
):
    """Журнал действий: что сделано, куда, чем закончилось, ссылка на живой пост."""
    query = select(PromoAction).order_by(PromoAction.id.desc()).limit(limit)
    if channel:
        query = query.where(PromoAction.channel == channel)
    if status:
        query = query.where(PromoAction.status == status)

    rows = (await db.execute(query)).scalars().all()
    return {"actions": [row.to_dict() for row in rows], "total": len(rows)}


@router.get("/settings")
async def get_settings(db: AsyncSession = Depends(get_db_session)):
    """Текущие настройки + признак глобального kill-switch."""
    settings = await _settings_row(db)
    return {
        "settings": settings.to_dict(),
        "module_enabled": not promo_disabled(),
        "kill_switch_note": (
            "PROMO_DISABLED в env перекрывает любые переключатели ниже: "
            "пока он взведён, каналы не публикуют"
        ),
    }


@router.put("/settings")
async def put_settings(payload: SettingsPut, db: AsyncSession = Depends(get_db_session)):
    """Частичное обновление: переданы только изменившиеся поля, ``None`` не трогаем."""
    settings = await _settings_row(db)

    for field in (
        "threshold_members",
        "graduate_members",
        "donor_min_members",
        "max_per_donor_per_week",
        "max_per_target_per_week",
        "max_actions_per_day",
        "quiet_hours_start",
        "quiet_hours_end",
        "second_hop_enabled",
        "oblast_fallback_enabled",
        "channels",
    ):
        value = getattr(payload, field)
        if value is not None:
            setattr(settings, field, value)

    # Гистерезис обязан остаться гистерезисом: порог выхода ниже порога входа
    # заставил бы район мигать вход/выход каждую ночь.
    if settings.graduate_members < settings.threshold_members:
        settings.graduate_members = settings.threshold_members

    await db.commit()
    await db.refresh(settings)
    return {"settings": settings.to_dict()}


@router.get("/outreach")
async def list_outreach(
    db: AsyncSession = Depends(get_db_session),
    region_code: Optional[str] = None,
    limit: int = Query(100, ge=1, le=300),
):
    """Кандидаты для РУЧНОГО обращения владельца. SETKA ничего не отправляет."""
    query = (
        select(PromoOutreachCandidate)
        .order_by(PromoOutreachCandidate.score.desc().nullslast())
        .limit(limit)
    )
    if region_code:
        region = (
            await db.execute(select(Region).where(Region.code == region_code))
        ).scalar_one_or_none()
        if region is None:
            raise HTTPException(status_code=404, detail="Регион не найден")
        query = query.where(PromoOutreachCandidate.target_region_id == region.id)

    rows = (await db.execute(query)).scalars().all()
    return {
        "candidates": [row.to_dict() for row in rows],
        "total": len(rows),
        "note": "SETKA не отправляет сообщения — только готовит текст для отправки вручную",
    }
