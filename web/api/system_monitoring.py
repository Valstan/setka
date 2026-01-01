"""
System Monitoring API - Real-time monitoring of SETKA system operations
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, desc
from typing import Dict, List, Optional
from datetime import datetime, timedelta
from pydantic import BaseModel
import logging
import psutil
import asyncio

from database.connection import get_db_session
from database.models import Region, Community, Post, VKToken
from config.runtime import PRODUCTION_WORKFLOW_CONFIG
from modules.operation_tracking import operation_tracker
from utils.cache import cache
from utils.timezone import now_moscow, get_moscow_hour, is_work_hours_moscow, format_moscow_time, moscow_to_utc
from modules.system_status_notifier import system_status_notifier

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
            select(func.count(Region.id)).where(Region.is_active == True)
        )
        active_regions = active_regions_result.scalar() or 0
        
        # Get communities count
        communities_result = await db.execute(
            select(func.count(Community.id)).where(Community.is_active == True)
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
            select(func.count(VKToken.id)).where(VKToken.is_active == True)
        )
        vk_tokens_active = active_vk_tokens_result.scalar() or 0
        
        # Get system resources
        # Avoid blocking the event loop; interval=0 returns last computed value (non-blocking).
        cpu_usage = psutil.cpu_percent(interval=0)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        
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
                "next_scheduled_run": workflow_status.get("next_run")
            }
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
            select(Post).where(Post.created_at >= now_utc - timedelta(minutes=30))
            .order_by(desc(Post.created_at))
            .limit(10)
        )
        recent_posts = recent_posts_result.scalars().all()
        
        # Get active regions
        active_regions_result = await db.execute(
            select(Region).where(Region.is_active == True)
        )
        active_regions = active_regions_result.scalars().all()
        
        # Get tracked operations (both active and recent)
        active_operations = operation_tracker.get_active_operations()
        recent_operations = operation_tracker.get_recent_operations(limit=10)
        
        # Convert tracked operations to API format
        operations = []
        
        # Add active operations first
        for op in active_operations:
            operations.append({
                "type": op["type"],
                "status": op["status"],
                "description": op["description"],
                "region": op.get("region"),
                "timestamp": op["start_time"].isoformat(),
                "end_time": op.get("end_time").isoformat() if op.get("end_time") else None,
                "duration": None,  # Will calculate for active operations
                "details": op.get("details", {})
            })
        
        # Add recent completed operations
        for op in recent_operations:
            if op["status"] != "active":  # Skip active ones, already added
                duration = None
                if op.get("end_time") and op.get("start_time"):
                    duration = (op["end_time"] - op["start_time"]).total_seconds()
                
                operations.append({
                    "type": op["type"],
                    "status": op["status"],
                    "description": op["description"],
                    "region": op.get("region"),
                    "timestamp": op["start_time"].isoformat(),
                    "end_time": op.get("end_time").isoformat() if op.get("end_time") else None,
                    "duration": duration,
                    "details": op.get("details", {})
                })
        
        # Add recent activity if no tracked operations
        if not operations and recent_posts:
            latest_post = recent_posts[0]
            operations.append({
                "type": "monitoring",
                "status": "recent",
                "description": f"Недавний мониторинг региона {latest_post.region.code}",
                "region": latest_post.region.code,
                "timestamp": latest_post.created_at.isoformat(),
                "end_time": None,
                "duration": None,
                "details": {
                    "posts_collected": len(recent_posts),
                    "latest_post_id": latest_post.id
                }
            })
        
        # Check for scheduled workflow
        work_hours_start = PRODUCTION_WORKFLOW_CONFIG.get('work_hours_start', 7)
        work_hours_end = PRODUCTION_WORKFLOW_CONFIG.get('work_hours_end', 22)
        current_hour = get_moscow_hour()
        
        if is_work_hours_moscow(work_hours_start, work_hours_end):
            operations.append({
                "type": "scheduled_workflow",
                "status": "scheduled",
                "description": "Автоматическая карусель обработки активна",
                "timestamp": now_moscow().isoformat(),
                "end_time": None,
                "duration": None,
                "details": {
                    "active_regions": len(active_regions),
                    "work_hours": f"{work_hours_start}:00-{work_hours_end}:00 MSK"
                }
            })
        else:
            operations.append({
                "type": "scheduled_workflow",
                "status": "paused",
                "description": "Автоматическая карусель приостановлена (вне рабочих часов)",
                "timestamp": now_moscow().isoformat(),
                "end_time": None,
                "duration": None,
                "details": {
                    "work_hours": f"{work_hours_start}:00-{work_hours_end}:00 MSK",
                    "current_hour": f"{current_hour}:00 MSK"
                }
            })
        
        # Sort operations by timestamp (newest first)
        operations.sort(key=lambda x: x["timestamp"], reverse=True)
        
        return {
            "success": True,
            "data": {
                "operations": operations,
                "total_operations": len(operations),
                "active_operations": len([op for op in operations if op["status"] == "active"]),
                "timestamp": now_moscow().isoformat()
            }
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
        regions_result = await db.execute(
            select(Region).order_by(Region.code)
        )
        regions = regions_result.scalars().all()
        
        regions_status = []
        
        for region in regions:
            # Get communities count for region
            communities_count_result = await db.execute(
                select(func.count(Community.id))
                .where(and_(
                    Community.region_id == region.id,
                    Community.is_active == True
                ))
            )
            communities_count = communities_count_result.scalar() or 0
            
            # Get posts count for today
            today_start = moscow_to_utc(now_moscow()).replace(tzinfo=None).replace(hour=0, minute=0, second=0, microsecond=0)
            posts_today_result = await db.execute(
                select(func.count(Post.id))
                .where(and_(
                    Post.region_id == region.id,
                    Post.created_at >= today_start
                ))
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
            
            regions_status.append({
                "id": region.id,
                "code": region.code,
                "name": region.name,
                "is_active": region.is_active,
                "communities_count": communities_count,
                "posts_today": posts_today,
                "last_activity": last_post.created_at.isoformat() if last_post else None,
                "status": "active" if region.is_active else "paused"
            })
        
        return {
            "success": True,
            "data": {
                "regions": regions_status,
                "total_regions": len(regions),
                "active_regions": len([r for r in regions_status if r["is_active"]]),
                "timestamp": now_moscow().isoformat()
            }
        }
        
    except Exception as e:
        logger.error(f"Error getting regions status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/workflow-status", response_model=Dict)
async def get_workflow_status():
    """Get current workflow status"""
    try:
        # Check if Celery is running
        celery_status = "unknown"
        try:
            # Try to import and check Celery
            from celery_app import app
            celery_status = "running"
        except Exception:
            celery_status = "not_running"
        
        # Get work hours configuration
        work_hours_start = PRODUCTION_WORKFLOW_CONFIG.get('work_hours_start', 7)
        work_hours_end = PRODUCTION_WORKFLOW_CONFIG.get('work_hours_end', 22)
        
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
                "timestamp": now_moscow().isoformat()
            }
        }
        
    except Exception as e:
        logger.error(f"Error getting workflow status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/live", response_model=Dict)
async def get_live_monitoring(db: AsyncSession = Depends(get_db_session)):
    """Get live monitoring data (all stats combined)"""
    try:
        now = now_moscow()
        
        # Get all monitoring data sequentially to avoid concurrent session issues
        stats_result = await get_system_stats(db)
        operations_result = await get_current_operations(db)
        regions_result = await get_regions_status(db)
        workflow_result = await get_workflow_status()
        
        return {
            "success": True,
            "data": {
                "stats": stats_result.get("data", {}),
                "operations": operations_result.get("data", {}),
                "regions": regions_result.get("data", {}),
                "workflow": workflow_result.get("data", {}),
                "timestamp": now_moscow().isoformat()
            }
        }
        
    except Exception as e:
        logger.error(f"Error getting live monitoring: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Helper function for workflow status
async def get_workflow_status():
    """Get workflow status (helper function)"""
    try:
        work_hours_start = PRODUCTION_WORKFLOW_CONFIG.get('work_hours_start', 7)
        work_hours_end = PRODUCTION_WORKFLOW_CONFIG.get('work_hours_end', 22)
        
        now = now_moscow()
        current_hour = get_moscow_hour()
        
        if is_work_hours_moscow(work_hours_start, work_hours_end):
            return {
                "status": "active",
                "current_operation": "scheduled_processing",
                "current_region": "carousel_mode",
                "last_run": "running_hourly",
                "next_run": "next_hour"
            }
        else:
            return {
                "status": "paused",
                "current_operation": None,
                "current_region": None,
                "last_run": None,
                "next_run": f"{work_hours_start}:00 MSK"
            }
    except Exception:
        return {
            "status": "unknown",
            "current_operation": None,
            "current_region": None,
            "last_run": None,
            "next_run": None
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
                "timestamp": now_moscow().isoformat()
            }
        }
        
    except Exception as e:
        logger.error(f"Error getting system status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


