"""
API endpoints for Celery task monitoring
"""
from fastapi import APIRouter, Depends
from typing import Dict, List, Any
import logging

from modules.celery_task_monitor import celery_task_monitor
from utils.timezone import now_moscow

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/monitoring/tasks", tags=["Task Monitoring"])


@router.get("/recent", response_model=Dict)
async def get_recent_tasks(limit: int = 20):
    """Получить недавние задачи"""
    try:
        tasks = celery_task_monitor.get_recent_tasks(limit)
        formatted_tasks = [celery_task_monitor.format_task_for_display(task) for task in tasks]
        
        return {
            "success": True,
            "data": {
                "tasks": formatted_tasks,
                "count": len(formatted_tasks),
                "timestamp": now_moscow().isoformat()
            }
        }
    except Exception as e:
        logger.error(f"Error getting recent tasks: {e}")
        return {
            "success": False,
            "error": str(e)
        }


@router.get("/active", response_model=Dict)
async def get_active_tasks():
    """Получить активные задачи"""
    try:
        tasks = celery_task_monitor.get_active_tasks()
        formatted_tasks = [celery_task_monitor.format_task_for_display(task) for task in tasks]
        
        return {
            "success": True,
            "data": {
                "tasks": formatted_tasks,
                "count": len(formatted_tasks),
                "timestamp": now_moscow().isoformat()
            }
        }
    except Exception as e:
        logger.error(f"Error getting active tasks: {e}")
        return {
            "success": False,
            "error": str(e)
        }


@router.get("/scheduled", response_model=Dict)
async def get_scheduled_tasks():
    """Получить запланированные задачи"""
    try:
        tasks = celery_task_monitor.get_scheduled_tasks()
        formatted_tasks = [celery_task_monitor.format_task_for_display(task) for task in tasks]
        
        return {
            "success": True,
            "data": {
                "tasks": formatted_tasks,
                "count": len(formatted_tasks),
                "timestamp": now_moscow().isoformat()
            }
        }
    except Exception as e:
        logger.error(f"Error getting scheduled tasks: {e}")
        return {
            "success": False,
            "error": str(e)
        }


@router.get("/statistics", response_model=Dict)
async def get_task_statistics():
    """Получить статистику задач"""
    try:
        stats = celery_task_monitor.get_task_statistics()
        
        return {
            "success": True,
            "data": {
                "statistics": stats,
                "timestamp": now_moscow().isoformat()
            }
        }
    except Exception as e:
        logger.error(f"Error getting task statistics: {e}")
        return {
            "success": False,
            "error": str(e)
        }


@router.get("/overview", response_model=Dict)
async def get_tasks_overview():
    """Получить обзор всех задач (завершенные, активные, запланированные)"""
    try:
        recent_tasks = celery_task_monitor.get_recent_tasks(10)
        active_tasks = celery_task_monitor.get_active_tasks()
        scheduled_tasks = celery_task_monitor.get_scheduled_tasks()
        stats = celery_task_monitor.get_task_statistics()
        
        # Форматируем задачи
        formatted_recent = [celery_task_monitor.format_task_for_display(task) for task in recent_tasks]
        formatted_active = [celery_task_monitor.format_task_for_display(task) for task in active_tasks]
        formatted_scheduled = [celery_task_monitor.format_task_for_display(task) for task in scheduled_tasks]
        
        return {
            "success": True,
            "data": {
                "completed_tasks": {
                    "tasks": formatted_recent,
                    "count": len(formatted_recent)
                },
                "active_tasks": {
                    "tasks": formatted_active,
                    "count": len(formatted_active)
                },
                "scheduled_tasks": {
                    "tasks": formatted_scheduled,
                    "count": len(formatted_scheduled)
                },
                "statistics": stats,
                "timestamp": now_moscow().isoformat()
            }
        }
    except Exception as e:
        logger.error(f"Error getting tasks overview: {e}")
        return {
            "success": False,
            "error": str(e)
        }
