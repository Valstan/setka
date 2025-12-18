"""
Real Workflow API - API для запуска реального workflow системы
"""
from fastapi import APIRouter, HTTPException, BackgroundTasks
from typing import Dict
import asyncio
import logging

from modules.service_notifications import service_notifications
from modules.real_workflow import real_workflow_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/test-workflow", tags=["test-workflow"])


async def run_real_workflow():
    """Запуск реального workflow системы"""
    try:
        logger.info("Starting real workflow...")
        
        # Запускаем реальный workflow для региона Малмыж
        success = await real_workflow_manager.start_real_workflow("mi")
        
        if success:
            logger.info("Real workflow completed successfully")
            service_notifications.success("Реальный workflow завершён успешно")
        else:
            logger.error("Real workflow failed")
            service_notifications.error("Реальный workflow завершился с ошибкой")
        
    except Exception as e:
        logger.error(f"Error in real workflow: {e}")
        service_notifications.error(f"Ошибка в реальном workflow: {str(e)}")


@router.post("/start")
async def start_test_workflow(background_tasks: BackgroundTasks):
    """Запустить реальный workflow"""
    try:
        # Проверяем, не запущен ли уже workflow
        if real_workflow_manager.is_running:
            raise HTTPException(status_code=400, detail="Workflow уже запущен")
        
        # Запускаем реальный workflow в фоне
        background_tasks.add_task(run_real_workflow)
        
        return {
            "success": True,
            "message": "Реальный workflow запущен",
            "data": {
                "status": "started",
                "type": "real",
                "timestamp": service_notifications.notifications[-1].timestamp.isoformat() if service_notifications.notifications else None
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error starting real workflow: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/stop")
async def stop_test_workflow():
    """Остановить workflow"""
    try:
        real_workflow_manager.is_running = False
        service_notifications.system_pause()
        
        return {
            "success": True,
            "message": "Workflow остановлен",
            "data": {
                "status": "stopped",
                "timestamp": service_notifications.notifications[-1].timestamp.isoformat() if service_notifications.notifications else None
            }
        }
        
    except Exception as e:
        logger.error(f"Error stopping workflow: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status")
async def get_workflow_status():
    """Получить статус workflow"""
    try:
        status = real_workflow_manager.get_status()
        notifications_status = service_notifications.get_status()
        
        return {
            "success": True,
            "data": {
                **status,
                "notifications": notifications_status
            }
        }
        
    except Exception as e:
        logger.error(f"Error getting workflow status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/scan-region/{region_code}")
async def scan_specific_region(region_code: str, background_tasks: BackgroundTasks):
    """Сканировать конкретный регион"""
    try:
        if real_workflow_manager.is_running:
            raise HTTPException(status_code=400, detail="Workflow уже запущен")
        
        async def scan_region():
            await real_workflow_manager.start_real_workflow(region_code)
        
        background_tasks.add_task(scan_region)
        
        return {
            "success": True,
            "message": f"Сканирование региона {region_code} запущено",
            "data": {
                "region": region_code,
                "status": "started"
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error scanning region {region_code}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
