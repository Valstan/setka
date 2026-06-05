"""
System Monitoring API - Real-time monitoring of SETKA system operations
"""

import logging
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import psutil
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import and_, case, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from config.runtime import PRODUCTION_WORKFLOW_CONFIG
from database.connection import get_db_session
from database.models import Community, Post, Region, VKToken
from database.models_extended import ParsingStats
from modules.operation_tracking import operation_tracker
from modules.system_status_notifier import system_status_notifier
from utils.cache import cache
from utils.timezone import get_moscow_hour, is_work_hours_moscow, moscow_to_utc, now_moscow

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/monitoring", tags=["system-monitoring"])


class SystemStats(BaseModel):
    """System statistics model"""

    timestamp: str
    system_status: str
    current_operation: Optional[str] = None
    current_region: Optional[str] = None
    active_regions_count: int
    total_regions_count: int
    communities_count: int
    posts_today: int
    posts_last_hour: int
    vk_tokens_active: int
    vk_tokens_total: int
    cpu_usage: float
    memory_usage: float
    disk_usage: float
    workflow_status: str
    last_workflow_run: Optional[str] = None
    next_scheduled_run: Optional[str] = None


@router.get("/stats", response_model=Dict)
@cache(ttl=60, key_prefix="monitoring")  # Cache for 1 minute
async def get_system_stats(db: AsyncSession = Depends(get_db_session)):
    """Get comprehensive system statistics"""
    try:
        now = now_moscow()
        now_utc = moscow_to_utc(now).replace(tzinfo=None)  # Convert to naive datetime for DB
        today_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
        last_hour = now_utc - timedelta(hours=1)

        # Get regions stats
        total_regions_result = await db.execute(select(func.count(Region.id)))
        total_regions = total_regions_result.scalar() or 0

        active_regions_result = await db.execute(
            select(func.count(Region.id)).where(Region.is_active.is_(True))
        )
        active_regions = active_regions_result.scalar() or 0

        # Get communities count
        communities_result = await db.execute(
            select(func.count(Community.id)).where(Community.is_active.is_(True))
        )
        communities_count = communities_result.scalar() or 0

        # Get posts stats
        posts_today_result = await db.execute(
            select(func.count(Post.id)).where(Post.created_at >= today_start)
        )
        posts_today = posts_today_result.scalar() or 0

        posts_last_hour_result = await db.execute(
            select(func.count(Post.id)).where(Post.created_at >= last_hour)
        )
        posts_last_hour = posts_last_hour_result.scalar() or 0

        # Get VK tokens stats
        vk_tokens_result = await db.execute(select(func.count(VKToken.id)))
        vk_tokens_total = vk_tokens_result.scalar() or 0

        active_vk_tokens_result = await db.execute(
            select(func.count(VKToken.id)).where(VKToken.is_active.is_(True))
        )
        vk_tokens_active = active_vk_tokens_result.scalar() or 0

        # Get system resources
        # Avoid blocking the event loop; interval=0 returns last computed value (non-blocking).
        cpu_usage = psutil.cpu_percent(interval=0)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage("/")

        # Determine system status
        system_status = "healthy"
        if cpu_usage > 80:
            system_status = "warning"
        if memory.percent > 90:
            system_status = "critical"

        # Get workflow status
        workflow_status = await get_workflow_status()

        return {
            "success": True,
            "data": {
                "timestamp": now_moscow().isoformat(),
                "system_status": system_status,
                "current_operation": workflow_status.get("current_operation"),
                "current_region": workflow_status.get("current_region"),
                "active_regions_count": active_regions,
                "total_regions_count": total_regions,
                "communities_count": communities_count,
                "posts_today": posts_today,
                "posts_last_hour": posts_last_hour,
                "vk_tokens_active": vk_tokens_active,
                "vk_tokens_total": vk_tokens_total,
                "cpu_usage": round(cpu_usage, 1),
                "memory_usage": round(memory.percent, 1),
                "disk_usage": round(disk.percent, 1),
                "workflow_status": workflow_status.get("status", "idle"),
                "last_workflow_run": workflow_status.get("last_run"),
                "next_scheduled_run": workflow_status.get("next_run"),
            },
        }

    except Exception as e:
        logger.error(f"Error getting system stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/operations", response_model=Dict)
async def get_current_operations(db: AsyncSession = Depends(get_db_session)):
    """Get current system operations with history"""
    try:
        now = now_moscow()
        now_utc = moscow_to_utc(now).replace(tzinfo=None)  # Convert to naive datetime for DB

        # Get recent posts to determine current activity
        recent_posts_result = await db.execute(
            select(Post)
            .where(Post.created_at >= now_utc - timedelta(minutes=30))
            .order_by(desc(Post.created_at))
            .limit(10)
        )
        recent_posts = recent_posts_result.scalars().all()

        # Get active regions
        active_regions_result = await db.execute(select(Region).where(Region.is_active.is_(True)))
        active_regions = active_regions_result.scalars().all()

        # Get tracked operations (both active and recent)
        active_operations = operation_tracker.get_active_operations()
        recent_operations = operation_tracker.get_recent_operations(limit=10)

        # Convert tracked operations to API format
        operations = []

        # Add active operations first
        for op in active_operations:
            operations.append(
                {
                    "type": op["type"],
                    "status": op["status"],
                    "description": op["description"],
                    "region": op.get("region"),
                    "timestamp": op["start_time"].isoformat(),
                    "end_time": op.get("end_time").isoformat() if op.get("end_time") else None,
                    "duration": None,  # Will calculate for active operations
                    "details": op.get("details", {}),
                }
            )

        # Add recent completed operations
        for op in recent_operations:
            if op["status"] != "active":  # Skip active ones, already added
                duration = None
                if op.get("end_time") and op.get("start_time"):
                    duration = (op["end_time"] - op["start_time"]).total_seconds()

                operations.append(
                    {
                        "type": op["type"],
                        "status": op["status"],
                        "description": op["description"],
                        "region": op.get("region"),
                        "timestamp": op["start_time"].isoformat(),
                        "end_time": op.get("end_time").isoformat() if op.get("end_time") else None,
                        "duration": duration,
                        "details": op.get("details", {}),
                    }
                )

        # Add recent activity if no tracked operations
        if not operations and recent_posts:
            latest_post = recent_posts[0]
            operations.append(
                {
                    "type": "monitoring",
                    "status": "recent",
                    "description": f"Недавний мониторинг региона {latest_post.region.code}",
                    "region": latest_post.region.code,
                    "timestamp": latest_post.created_at.isoformat(),
                    "end_time": None,
                    "duration": None,
                    "details": {
                        "posts_collected": len(recent_posts),
                        "latest_post_id": latest_post.id,
                    },
                }
            )

        # Check for scheduled workflow
        work_hours_start = PRODUCTION_WORKFLOW_CONFIG.get("work_hours_start", 7)
        work_hours_end = PRODUCTION_WORKFLOW_CONFIG.get("work_hours_end", 22)
        current_hour = get_moscow_hour()

        if is_work_hours_moscow(work_hours_start, work_hours_end):
            operations.append(
                {
                    "type": "scheduled_workflow",
                    "status": "scheduled",
                    "description": "Автоматическая карусель обработки активна",
                    "timestamp": now_moscow().isoformat(),
                    "end_time": None,
                    "duration": None,
                    "details": {
                        "active_regions": len(active_regions),
                        "work_hours": f"{work_hours_start}:00-{work_hours_end}:00 MSK",
                    },
                }
            )
        else:
            operations.append(
                {
                    "type": "scheduled_workflow",
                    "status": "paused",
                    "description": "Автоматическая карусель приостановлена (вне рабочих часов)",
                    "timestamp": now_moscow().isoformat(),
                    "end_time": None,
                    "duration": None,
                    "details": {
                        "work_hours": f"{work_hours_start}:00-{work_hours_end}:00 MSK",
                        "current_hour": f"{current_hour}:00 MSK",
                    },
                }
            )

        # Sort operations by timestamp (newest first)
        operations.sort(key=lambda x: x["timestamp"], reverse=True)

        return {
            "success": True,
            "data": {
                "operations": operations,
                "total_operations": len(operations),
                "active_operations": len([op for op in operations if op["status"] == "active"]),
                "timestamp": now_moscow().isoformat(),
            },
        }

    except Exception as e:
        logger.error(f"Error getting current operations: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/regions-status", response_model=Dict)
@cache(ttl=120, key_prefix="monitoring")  # Cache for 2 minutes
async def get_regions_status(db: AsyncSession = Depends(get_db_session)):
    """Get detailed status of all regions"""
    try:
        # Get all regions with their stats
        regions_result = await db.execute(select(Region).order_by(Region.code))
        regions = regions_result.scalars().all()

        regions_status = []

        for region in regions:
            # Get communities count for region
            communities_count_result = await db.execute(
                select(func.count(Community.id)).where(
                    and_(Community.region_id == region.id, Community.is_active.is_(True))
                )
            )
            communities_count = communities_count_result.scalar() or 0

            # Get posts count for today
            today_start = (
                moscow_to_utc(now_moscow())
                .replace(tzinfo=None)
                .replace(hour=0, minute=0, second=0, microsecond=0)
            )
            posts_today_result = await db.execute(
                select(func.count(Post.id)).where(
                    and_(Post.region_id == region.id, Post.created_at >= today_start)
                )
            )
            posts_today = posts_today_result.scalar() or 0

            # Get last activity
            last_post_result = await db.execute(
                select(Post)
                .where(Post.region_id == region.id)
                .order_by(desc(Post.created_at))
                .limit(1)
            )
            last_post = last_post_result.scalar_one_or_none()

            regions_status.append(
                {
                    "id": region.id,
                    "code": region.code,
                    "name": region.name,
                    "is_active": region.is_active,
                    "communities_count": communities_count,
                    "posts_today": posts_today,
                    "last_activity": last_post.created_at.isoformat() if last_post else None,
                    "status": "active" if region.is_active else "paused",
                }
            )

        return {
            "success": True,
            "data": {
                "regions": regions_status,
                "total_regions": len(regions),
                "active_regions": len([r for r in regions_status if r["is_active"]]),
                "timestamp": now_moscow().isoformat(),
            },
        }

    except Exception as e:
        logger.error(f"Error getting regions status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Пороги «свежести» дайджеста (в часах). Для каждой пары (region_code, theme)
# берём last_run_date из parsing_stats и красим:
#   fresh   — last_run < FRESH_HOURS назад
#   stale   — last_run между FRESH_HOURS и STALE_HOURS
#   dead    — last_run > STALE_HOURS назад (или вообще нет за 30 дней)
#   broken  — формально run был свежим, но последние N подряд failed
#             (success=false), что значит beat жив, а pipeline валится
_DIGEST_STATUS_FRESH_HOURS = 12
_DIGEST_STATUS_STALE_HOURS = 24
_DIGEST_STATUS_BROKEN_MIN_FAILED_RUNS = 3


def _classify_digest_row(
    last_run_at: Optional[datetime],
    last_success_at: Optional[datetime],
    consecutive_failed: int,
    now_utc: datetime,
) -> str:
    if last_run_at is None:
        return "dead"
    age_hours = (now_utc - last_run_at).total_seconds() / 3600.0
    if (
        consecutive_failed >= _DIGEST_STATUS_BROKEN_MIN_FAILED_RUNS
        and age_hours <= _DIGEST_STATUS_STALE_HOURS
    ):
        # Beat запускает таску регулярно, но она падает — это «broken», не «dead».
        return "broken"
    if last_success_at is None:
        return "dead"
    success_age_hours = (now_utc - last_success_at).total_seconds() / 3600.0
    if success_age_hours <= _DIGEST_STATUS_FRESH_HOURS:
        return "fresh"
    if success_age_hours <= _DIGEST_STATUS_STALE_HOURS:
        return "stale"
    return "dead"


@router.get("/digests-status", response_model=Dict)
@cache(ttl=60, key_prefix="monitoring")
async def get_digests_status(db: AsyncSession = Depends(get_db_session)):
    """Свод состояния дайджестов по (region_code, theme) из ``parsing_stats``.

    Источник — ``parsing_stats`` (та же таблица, что и для `/parsing-stats`).
    Возвращаем для каждой пары region×theme: время последней beat-таски,
    время последнего успеха, количество запусков и опубликованных постов
    за 24 часа, и категорию ``status`` (fresh/stale/broken/dead) для
    цветовой маркировки в UI.

    Используется виджетами на `/monitoring` и главной странице. Полная
    история и фильтры — на `/parsing-stats`.
    """
    now_utc = datetime.utcnow()
    since_24h = now_utc - timedelta(hours=24)
    since_30d = now_utc - timedelta(days=30)

    # ── 1. Базовый агрегат: для каждой (region_code, theme) за 30 дней ────
    success_int = case((ParsingStats.success.is_(True), 1), else_=0)
    last_success_run = case(
        (ParsingStats.success.is_(True), ParsingStats.run_date),
        else_=None,
    )
    runs_24h_flag = case((ParsingStats.run_date >= since_24h, 1), else_=0)
    posts_24h_expr = case(
        (ParsingStats.run_date >= since_24h, ParsingStats.posts_final_count),
        else_=0,
    )

    agg_stmt = (
        select(
            ParsingStats.region_code,
            ParsingStats.theme,
            func.max(ParsingStats.run_date).label("last_run_date"),
            func.max(last_success_run).label("last_success_date"),
            func.sum(runs_24h_flag).label("runs_24h"),
            func.sum(posts_24h_expr).label("posts_24h"),
            func.count(ParsingStats.id).label("runs_30d"),
            func.sum(success_int).label("success_30d"),
        )
        .where(ParsingStats.run_date >= since_30d)
        .group_by(ParsingStats.region_code, ParsingStats.theme)
    )
    agg_rows = (await db.execute(agg_stmt)).all()

    # ── 2. Подсчёт consecutive_failed по каждой паре (последние N задач) ──
    # Берём последние 10 задач per (region_code, theme), считаем сколько подряд
    # success=false с конца. Если >=3 — это «broken».
    rn_col = (
        func.row_number()
        .over(
            partition_by=(ParsingStats.region_code, ParsingStats.theme),
            order_by=ParsingStats.run_date.desc(),
        )
        .label("rn")
    )
    recent_stmt = select(
        ParsingStats.region_code,
        ParsingStats.theme,
        ParsingStats.run_date,
        ParsingStats.success,
        rn_col,
    ).where(ParsingStats.run_date >= since_30d)
    recent_subq = recent_stmt.subquery()
    recent_rows = (
        await db.execute(
            select(
                recent_subq.c.region_code,
                recent_subq.c.theme,
                recent_subq.c.success,
                recent_subq.c.run_date,
            )
            .where(recent_subq.c.rn <= 10)
            .order_by(
                recent_subq.c.region_code,
                recent_subq.c.theme,
                recent_subq.c.run_date.desc(),
            )
        )
    ).all()
    consecutive_failed: Dict[tuple, int] = {}
    for r in recent_rows:
        key = (r.region_code, r.theme)
        if key in consecutive_failed:
            continue  # already finalized (нашли первый success или предел)
        cnt = consecutive_failed.get(key, 0)
        if r.success:
            consecutive_failed[key] = cnt  # finalize
            continue
        consecutive_failed[key] = cnt + 1
    # finalize все пары, у которых row нашёлся, но успехов не было — счётчик
    # уже корректно зафиксирован в loop'е через _key.

    # ── 3. Region.name lookup ─────────────────────────────────────────────
    regions_result = await db.execute(select(Region.code, Region.name, Region.is_active))
    region_meta = {r.code: {"name": r.name, "is_active": r.is_active} for r in regions_result}

    # ── 4. Склейка ────────────────────────────────────────────────────────
    rows = []
    counters = {"fresh": 0, "stale": 0, "broken": 0, "dead": 0}
    for r in agg_rows:
        key = (r.region_code, r.theme)
        cf = consecutive_failed.get(key, 0)
        status = _classify_digest_row(
            last_run_at=r.last_run_date,
            last_success_at=r.last_success_date,
            consecutive_failed=cf,
            now_utc=now_utc,
        )
        counters[status] = counters.get(status, 0) + 1
        meta = region_meta.get(r.region_code, {})
        rows.append(
            {
                "region_code": r.region_code,
                "region_name": meta.get("name") or r.region_code,
                "region_is_active": bool(meta.get("is_active", True)),
                "theme": r.theme,
                "last_run_date": r.last_run_date.isoformat() if r.last_run_date else None,
                "last_success_date": (
                    r.last_success_date.isoformat() if r.last_success_date else None
                ),
                "runs_24h": int(r.runs_24h or 0),
                "posts_24h": int(r.posts_24h or 0),
                "runs_30d": int(r.runs_30d or 0),
                "success_30d": int(r.success_30d or 0),
                "consecutive_failed": cf,
                "status": status,
            }
        )

    # Sort: broken first (внимание!), затем dead, stale, fresh; внутри — по
    # давности last_run_date (старые впереди).
    status_order = {"broken": 0, "dead": 1, "stale": 2, "fresh": 3}
    rows.sort(
        key=lambda r: (
            status_order.get(r["status"], 9),
            -(datetime.fromisoformat(r["last_run_date"]).timestamp() if r["last_run_date"] else 0),
            r["region_code"],
            r["theme"],
        )
    )

    return {
        "success": True,
        "data": {
            "rows": rows,
            "summary": {
                **counters,
                "total_pairs": len(rows),
            },
            "thresholds": {
                "fresh_hours": _DIGEST_STATUS_FRESH_HOURS,
                "stale_hours": _DIGEST_STATUS_STALE_HOURS,
                "broken_min_failed_runs": _DIGEST_STATUS_BROKEN_MIN_FAILED_RUNS,
            },
            "as_of": now_utc.isoformat() + "Z",
        },
    }


@router.get("/workflow-status", response_model=Dict)
async def get_workflow_status():
    """Get current workflow status"""
    try:
        # Check if Celery is running
        celery_status = "unknown"
        try:
            # Try to import and check Celery
            pass

            celery_status = "running"
        except Exception:
            celery_status = "not_running"

        # Get work hours configuration
        work_hours_start = PRODUCTION_WORKFLOW_CONFIG.get("work_hours_start", 7)
        work_hours_end = PRODUCTION_WORKFLOW_CONFIG.get("work_hours_end", 22)

        # Use Moscow time for work hours check
        current_hour = get_moscow_hour()

        # Determine workflow status
        if is_work_hours_moscow(work_hours_start, work_hours_end):
            workflow_status = "active"
            next_run = "Каждый час в рабочее время"
        else:
            workflow_status = "paused"
            next_run = f"Следующий запуск в {work_hours_start}:00 MSK"

        return {
            "success": True,
            "data": {
                "status": workflow_status,
                "celery_status": celery_status,
                "work_hours": f"{work_hours_start}:00-{work_hours_end}:00 MSK",
                "current_hour": f"{current_hour}:00 MSK",
                "next_run": next_run,
                "timestamp": now_moscow().isoformat(),
            },
        }

    except Exception as e:
        logger.error(f"Error getting workflow status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/live", response_model=Dict)
async def get_live_monitoring(db: AsyncSession = Depends(get_db_session)):
    """Get live monitoring data (all stats combined)"""
    try:
        # Get all monitoring data sequentially to avoid concurrent session issues
        stats_result = await get_system_stats(db)
        operations_result = await get_current_operations(db)
        regions_result = await get_regions_status(db)
        workflow_data = await _get_workflow_status_data()

        return {
            "success": True,
            "data": {
                "stats": stats_result.get("data", {}),
                "operations": operations_result.get("data", {}),
                "regions": regions_result.get("data", {}),
                "workflow": workflow_data,
                "timestamp": now_moscow().isoformat(),
            },
        }

    except Exception as e:
        logger.error(f"Error getting live monitoring: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Helper used by /live; the public endpoint at /workflow-status (defined above)
# adds a {success, data} wrapper and a celery_status / work_hours summary.
async def _get_workflow_status_data():
    try:
        work_hours_start = PRODUCTION_WORKFLOW_CONFIG.get("work_hours_start", 7)
        work_hours_end = PRODUCTION_WORKFLOW_CONFIG.get("work_hours_end", 22)

        if is_work_hours_moscow(work_hours_start, work_hours_end):
            return {
                "status": "active",
                "current_operation": "scheduled_processing",
                "current_region": "carousel_mode",
                "last_run": "running_hourly",
                "next_run": "next_hour",
            }
        else:
            return {
                "status": "paused",
                "current_operation": None,
                "current_region": None,
                "last_run": None,
                "next_run": f"{work_hours_start}:00 MSK",
            }
    except Exception:
        return {
            "status": "unknown",
            "current_operation": None,
            "current_region": None,
            "last_run": None,
            "next_run": None,
        }


@router.get("/system-status", response_model=Dict)
async def get_system_status():
    """Get current system status with notifications"""
    try:
        from utils.timezone import now_moscow

        # Проверяем статус карусели
        system_status_notifier.check_workflow_status()

        # Добавляем статус мониторинга
        system_status_notifier.add_monitoring_status()

        # Добавляем статус активности задач
        system_status_notifier.add_task_activity_status()

        # Получаем детальный статус с информацией о задачах
        status_summary = system_status_notifier.get_detailed_system_status()
        recent_notifications = system_status_notifier.get_recent_status_notifications(10)

        return {
            "success": True,
            "data": {
                "status_summary": status_summary,
                "recent_notifications": recent_notifications,
                "timestamp": now_moscow().isoformat(),
            },
        }

    except Exception as e:
        logger.error(f"Error getting system status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ──────────────────────────────────────────────────────────────────────────
# Heartbeat дайджестов (#018) + liveness воркеров/beat
#
# Heartbeat — Redis-ключи `setka:digest_last_published:<topic>` (см.
# modules/digest_heartbeat). Watchdog шлёт Telegram-алёрт при протухании
# `novost` (порог 6ч), но НИГДЕ не виден в UI — этот эндпоинт выводит его на
# дашборд (idea brain #018: «дашборд показывает liveness»).
# ──────────────────────────────────────────────────────────────────────────

# Порог свежести для ДИСПЛЕЯ дашборда (часы). Намеренно щедрее watchdog-порога
# novost (6ч): большинство тем публикуются ~раз в сутки, поэтому 26ч даёт
# суточной волне запас и не зажигает ложный «stale» из-за разной частоты тем.
# Строгий 6ч-порог применяется ОТДЕЛЬНО только к novost (как у beat-алёрта).
_HEARTBEAT_FRESH_HOURS = 26


def _classify_heartbeat_age(
    age_seconds: Optional[float], fresh_hours: float = _HEARTBEAT_FRESH_HOURS
) -> str:
    """Классифицировать возраст heartbeat для дисплея: unknown/fresh/stale.

    ``None`` (нет ключа в Redis) → ``unknown``: нельзя отличить «свежий деплой,
    волны ещё не было» от «сломано» — не пугаем красным (та же логика, что у
    watchdog'а #018, который на ``None`` не алёртит).
    """
    if age_seconds is None:
        return "unknown"
    if age_seconds < 0:
        age_seconds = 0
    return "fresh" if age_seconds < fresh_hours * 3600 else "stale"


@router.get("/heartbeat", response_model=Dict)
async def get_digest_heartbeat():
    """Redis-heartbeat последних публикаций по темам (#018) + статус watchdog.

    Возвращает по каждой теме время последней успешной публикации (из
    ``digest_heartbeat``), возраст и статус (fresh/stale/unknown), плюс
    отдельный блок ``watchdog`` — статус ``novost`` против строгого 6ч-порога,
    ровно того, по которому beat шлёт Telegram-алёрт. Темы без heartbeat
    показываются как ``unknown`` (никогда не публиковались / свежий деплой).
    """
    try:
        from modules import digest_heartbeat as dh
        from modules.digest_pipeline_settings import POSTOPUS_DIGEST_THEMES

        now_ts = time.time()
        hb = dh.all_heartbeats()  # topic -> unix-ts (best-effort)

        # Объединяем канонический список тем с тем, что реально есть в Redis
        # (на случай тем вне списка), сохраняя порядок и убирая дубли.
        topics: List[str] = list(dict.fromkeys(list(POSTOPUS_DIGEST_THEMES) + sorted(hb.keys())))

        rows = []
        for topic in topics:
            ts = hb.get(topic)
            age = (now_ts - ts) if ts is not None else None
            rows.append(
                {
                    "topic": topic,
                    "last_published_ts": ts,
                    "last_published_iso": (
                        datetime.utcfromtimestamp(ts).isoformat() + "Z" if ts is not None else None
                    ),
                    "age_seconds": int(age) if age is not None else None,
                    "status": _classify_heartbeat_age(age),
                }
            )

        # Сортировка: проблемные (stale) сверху, затем fresh (новее — выше),
        # unknown — в конце.
        _status_order = {"stale": 0, "fresh": 1, "unknown": 2}

        def _sort_key(r):
            st = r["status"]
            age = r["age_seconds"] or 0
            # внутри stale/fresh — старее выше (больший возраст важнее)
            return (_status_order.get(st, 3), -age if st != "unknown" else 0)

        rows.sort(key=_sort_key)

        # Watchdog novost — строгий 6ч-порог (как у beat-алёрта #018).
        wd_ts = hb.get("novost")
        wd_age = (now_ts - wd_ts) if wd_ts is not None else None
        wd_status = _classify_heartbeat_age(wd_age, fresh_hours=dh.DEFAULT_MAX_AGE_HOURS)

        return {
            "success": True,
            "data": {
                "topics": rows,
                "watchdog": {
                    "topic": "novost",
                    "max_age_hours": dh.DEFAULT_MAX_AGE_HOURS,
                    "status": wd_status,
                    "age_seconds": int(wd_age) if wd_age is not None else None,
                },
                "fresh_hours": _HEARTBEAT_FRESH_HOURS,
                "timestamp": now_moscow().isoformat(),
            },
        }
    except Exception as e:
        logger.error(f"Error getting digest heartbeat: {e}")
        return {"success": False, "error": str(e)}


@router.get("/liveness", response_model=Dict)
async def get_celery_liveness():
    """Liveness Celery: ping воркеров + инференс beat по ``novost``-heartbeat.

    Воркеры отвечают на ``inspect.ping()`` напрямую. **Beat не пингуется**
    (celery beat не отвечает на inspect), поэтому его живость выводим косвенно:
    ``novost`` публикуется ≥6×/сутки, так что свежий heartbeat = beat ставит
    задачи и worker их выполняет. Это инференс, помечен как таковой.
    """
    workers = []
    error = None
    try:
        from celery_app import app

        inspect = app.control.inspect(timeout=1.5)
        pong = inspect.ping() or {}
        for name, resp in pong.items():
            ok = bool(isinstance(resp, dict) and resp.get("ok") == "pong")
            workers.append({"name": name, "ok": ok})
    except Exception as e:  # pragma: no cover - сетевой/брокерный сбой
        error = str(e)
        logger.warning(f"celery ping failed: {e}")

    beat = {"status": "unknown", "note": "инференс по novost-heartbeat"}
    try:
        from modules import digest_heartbeat as dh

        ts = dh.last_published_ts("novost")
        if ts is not None:
            age = time.time() - ts
            beat["age_seconds"] = int(age)
            beat["status"] = "alive" if age < dh.DEFAULT_MAX_AGE_HOURS * 3600 else "stale"
    except Exception:  # pragma: no cover
        logger.debug("beat inference failed", exc_info=True)

    return {
        "success": error is None,
        "data": {
            "workers": workers,
            "worker_count": len(workers),
            "any_alive": any(w["ok"] for w in workers),
            "beat": beat,
            "timestamp": now_moscow().isoformat(),
        },
        **({"error": error} if error else {}),
    }
