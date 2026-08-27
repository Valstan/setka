"""Зачисление районов в раскрутку и починка их находимости — слой IO.

Модель pull, а не push: диспетчер сам добирает активные районы запросом на каждом
прогоне. Альтернатива — дописать зачисление в скрипт активации региона — выглядит
экономнее, но заводит ровно ту болезнь, от которой скрипт активации и лечили:
шаг, который можно забыть. Кэша активных регионов в процессах нет, публикация
выбирает регионы запросом к БД на каждом слоте, поэтому pull подхватывает новый
район за минуты и без рестарта.

Здесь же чинится главная находка замера 28.08: у 23 из 36 активных районов пусты
локальные хэштеги. Заполнение живёт в этом прогоне, а не в разовом UPDATE
миграции, — иначе следующий заведённый район снова окажется без тегов.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from config.promo import get_region_allowlist
from database.models import PromoEnrollment, PromoSettings, Region, RegionMemberSnapshot
from modules.promotion.enrollment import RegionState, evaluate_enrollment, summarize
from modules.promotion.hashtags import build_hashtag_plan

logger = logging.getLogger(__name__)


async def load_settings(session: AsyncSession) -> Dict[str, Any]:
    """Настройки раскрутки из БД; при отсутствии строки — дефолты модели."""
    row = (await session.execute(select(PromoSettings).limit(1))).scalar_one_or_none()
    if row is None:
        return {
            "threshold_members": 300,
            "graduate_members": 400,
            "donor_min_members": 1000,
            "max_actions_per_day": 3,
            "second_hop_enabled": True,
            "oblast_fallback_enabled": True,
            "channels": {},
        }
    return row.to_dict()


async def _latest_members(session: AsyncSession) -> Dict[int, int]:
    """Последний известный размер каждого региона: ``{region_id: members}``.

    Регион без снимка в словарь не попадает — и это отличие важно: отсутствие
    строки читается вызывающим как «не мерили», а не как ноль подписчиков.
    """
    latest = (
        select(
            RegionMemberSnapshot.region_id.label("region_id"),
            func.max(RegionMemberSnapshot.snapshot_date).label("day"),
        )
        .group_by(RegionMemberSnapshot.region_id)
        .subquery()
    )
    rows = await session.execute(
        select(RegionMemberSnapshot.region_id, RegionMemberSnapshot.members_count).join(
            latest,
            (RegionMemberSnapshot.region_id == latest.c.region_id)
            & (RegionMemberSnapshot.snapshot_date == latest.c.day),
        )
    )
    return {region_id: members for region_id, members in rows.all()}


async def _enrollment_status(session: AsyncSession) -> Dict[int, str]:
    rows = await session.execute(select(PromoEnrollment.region_id, PromoEnrollment.status))
    return {region_id: status for region_id, status in rows.all()}


async def sync_enrollments(session: AsyncSession, *, now: Optional[datetime] = None) -> Dict:
    """Привести состав раскрутки в соответствие с данными. Ничего не публикует.

    Returns:
        Сводка вида ``{"enrolled": N, "graduated": M, "kept": K, "hashtags_filled": H}``.
    """
    now = now or datetime.utcnow()
    settings = await load_settings(session)
    members = await _latest_members(session)
    statuses = await _enrollment_status(session)

    regions = (await session.execute(select(Region).where(Region.kind == "raion"))).scalars().all()

    states = [
        RegionState(
            region_id=r.id,
            code=r.code,
            kind=r.kind,
            is_active=bool(r.is_active),
            has_group=r.vk_group_id is not None,
            members=members.get(r.id),
            enrollment_status=statuses.get(r.id),
        )
        for r in regions
    ]

    decisions = evaluate_enrollment(
        states,
        threshold_members=int(settings.get("threshold_members") or 300),
        graduate_members=int(settings.get("graduate_members") or 400),
        allowlist=get_region_allowlist(),
        now=now,
    )

    for decision in decisions:
        if decision.action == "enroll":
            await session.execute(
                pg_insert(PromoEnrollment)
                .values(
                    region_id=decision.region_id,
                    status="active",
                    cohort="pending",
                    members_at_enroll=decision.members,
                    reason=decision.reason,
                    enrolled_at=now,
                    updated_at=now,
                )
                .on_conflict_do_nothing(index_elements=["region_id"])
            )
        elif decision.action == "graduate":
            await session.execute(
                update(PromoEnrollment)
                .where(
                    PromoEnrollment.region_id == decision.region_id,
                    PromoEnrollment.status == "active",
                )
                .values(
                    status="graduated",
                    members_at_graduate=decision.members,
                    reason=decision.reason,
                    graduated_at=now,
                    updated_at=now,
                )
            )

    filled = await fill_missing_hashtags(session, regions=regions)
    await session.commit()

    counts = summarize(decisions)
    result = {
        "enrolled": counts["enroll"],
        "graduated": counts["graduate"],
        "kept": counts["keep"],
        "hashtags_filled": len(filled),
        "hashtags_need_review": [p.region_code for p in filled if p.needs_review],
    }
    logger.info("promo enrollment sync: %s", result)
    return result


async def fill_missing_hashtags(
    session: AsyncSession, *, regions: Optional[List[Region]] = None
) -> List:
    """Проставить локальные хэштеги районам, у которых их нет. Ничего не перетирает.

    Райцентр берётся из ``regions.center_city``, а при его отсутствии — из
    ``region_configs.heshteg_local['raicentr']``: у новых районов заполнено
    именно второе. Район, для которого прилагательное неизвестно, получает
    **только** тег райцентра и помечается как требующий проверки — выдуманный
    хэштег уводит читателя в чужую выдачу и виден в каждом посте района.
    """
    from database.models_extended import RegionConfig  # локально: тяжёлый импорт

    if regions is None:
        regions = (
            (
                await session.execute(
                    select(Region).where(Region.kind == "raion", Region.is_active.is_(True))
                )
            )
            .scalars()
            .all()
        )

    pending = [r for r in regions if not (r.local_hashtags or "").strip()]
    if not pending:
        return []

    codes = [r.code for r in pending]
    config_rows = await session.execute(
        select(RegionConfig.region_code, RegionConfig.heshteg_local).where(
            RegionConfig.region_code.in_(codes)
        )
    )
    centers: Dict[str, str] = {}
    for code, heshteg_local in config_rows.all():
        if isinstance(heshteg_local, dict):
            value = heshteg_local.get("raicentr")
            if value:
                centers[code] = str(value)

    applied = []
    for region in pending:
        center = (region.center_city or "").strip() or centers.get(region.code, "")
        plan = build_hashtag_plan(region.code, center, existing=region.local_hashtags)
        if plan is None:
            continue
        await session.execute(
            update(Region).where(Region.id == region.id).values(local_hashtags=plan.as_field())
        )
        applied.append(plan)
        logger.info(
            "promo hashtags: %s → %s%s",
            region.code,
            plan.as_field(),
            " (нужна проверка)" if plan.needs_review else "",
        )
    return applied
