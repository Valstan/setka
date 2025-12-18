"""
VK Carousel Celery Tasks
Карусельные задачи для оптимизированного опроса регионов
"""
from celery import Celery
from celery.schedules import crontab
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List

from modules.vk_monitor.carousel_manager import carousel_manager
from database.connection import get_db_session
from config.config_secure import VK_TOKENS

logger = logging.getLogger(__name__)

# Получить Celery app из основного файла
from celery_app import app


@app.task(bind=True, name='vk_carousel.scan_next_region')
def scan_next_region_task(self):
    """
    Задача сканирования следующего региона в карусели
    
    Выполняется каждые 60 минут для поочередного опроса регионов
    """
    logger.info("Starting VK carousel region scan task")
    
    try:
        # Создать новую сессию БД
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        async def run_scan():
            async with get_db_session() as db:
                # Получить следующий регион для сканирования
                task = await carousel_manager.get_next_region_to_scan(db)
                
                if not task:
                    logger.warning("No regions available for scanning")
                    return {
                        "status": "skipped",
                        "reason": "no_regions_available",
                        "timestamp": datetime.now().isoformat()
                    }
                
                # Проверить, не превышен ли лимит активных сканирований
                if len(carousel_manager.active_scans) >= carousel_manager.max_concurrent_scans:
                    logger.warning(f"Maximum concurrent scans ({carousel_manager.max_concurrent_scans}) reached")
                    return {
                        "status": "skipped",
                        "reason": "max_concurrent_scans",
                        "timestamp": datetime.now().isoformat()
                    }
                
                # Выполнить сканирование
                success = await carousel_manager.execute_region_scan(task, db)
                
                if success:
                    logger.info(f"Successfully scanned region {task.region_code}: {task.posts_found} posts found")
                    return {
                        "status": "completed",
                        "region_code": task.region_code,
                        "region_name": task.region_name,
                        "posts_found": task.posts_found,
                        "token_used": task.token_name,
                        "duration_seconds": (task.completed_time - task.started_time).total_seconds() if task.completed_time and task.started_time else None,
                        "timestamp": datetime.now().isoformat()
                    }
                else:
                    logger.error(f"Failed to scan region {task.region_code}: {task.error_message}")
                    return {
                        "status": "failed",
                        "region_code": task.region_code,
                        "error_message": task.error_message,
                        "timestamp": datetime.now().isoformat()
                    }
        
        result = loop.run_until_complete(run_scan())
        loop.close()
        
        return result
        
    except Exception as e:
        logger.error(f"VK carousel scan task failed: {e}")
        return {
            "status": "error",
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }


@app.task(name='vk_carousel.validate_tokens')
def validate_tokens_task():
    """
    Задача проверки работоспособности токенов VK
    
    Выполняется каждые 6 часов
    """
    logger.info("Starting VK tokens validation task")
    
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        async def run_validation():
            from modules.vk_monitor.vk_client import VKClient
            
            results = {}
            valid_tokens = []
            
            for name, token in VK_TOKENS.items():
                if not token:
                    results[name] = {
                        "is_valid": False,
                        "error": "Token is empty"
                    }
                    continue
                
                try:
                    vk_client = VKClient(token)
                    user_info = await vk_client.get_user_info()
                    
                    if user_info:
                        results[name] = {
                            "is_valid": True,
                            "user_id": user_info.get('id'),
                            "user_name": f"{user_info.get('first_name', '')} {user_info.get('last_name', '')}".strip()
                        }
                        valid_tokens.append(name)
                    else:
                        results[name] = {
                            "is_valid": False,
                            "error": "Failed to get user info"
                        }
                        
                except Exception as e:
                    results[name] = {
                        "is_valid": False,
                        "error": str(e)
                    }
            
            logger.info(f"Token validation completed: {len(valid_tokens)}/{len(VK_TOKENS)} tokens are valid")
            
            return {
                "status": "completed",
                "valid_tokens": valid_tokens,
                "total_tokens": len(VK_TOKENS),
                "results": results,
                "timestamp": datetime.now().isoformat()
            }
        
        result = loop.run_until_complete(run_validation())
        loop.close()
        
        return result
        
    except Exception as e:
        logger.error(f"VK tokens validation task failed: {e}")
        return {
            "status": "error",
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }


@app.task(name='vk_carousel.optimize_frequency')
def optimize_frequency_task():
    """
    Задача оптимизации частоты сканирования
    
    Выполняется каждые 24 часа
    """
    logger.info("Starting VK carousel frequency optimization task")
    
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        async def run_optimization():
            async with get_db_session() as db:
                result = await carousel_manager.optimize_scan_frequency(db)
                
                logger.info(f"Frequency optimization completed: {result['recommended_interval_minutes']} minutes")
                
                return result
        
        result = loop.run_until_complete(run_optimization())
        loop.close()
        
        return result
        
    except Exception as e:
        logger.error(f"VK carousel frequency optimization task failed: {e}")
        return {
            "status": "error",
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }


@app.task(name='vk_carousel.get_status')
def get_carousel_status_task():
    """
    Задача получения статуса карусели
    
    Выполняется каждые 15 минут для мониторинга
    """
    logger.info("Getting VK carousel status")
    
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        async def run_status():
            async with get_db_session() as db:
                status = await carousel_manager.get_carousel_status(db)
                
                return {
                    "status": "success",
                    "data": status,
                    "timestamp": datetime.now().isoformat()
                }
        
        result = loop.run_until_complete(run_status())
        loop.close()
        
        return result
        
    except Exception as e:
        logger.error(f"VK carousel status task failed: {e}")
        return {
            "status": "error",
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }


# Настройка расписания Celery
app.conf.beat_schedule.update({
    'vk-carousel-scan': {
        'task': 'vk_carousel.scan_next_region',
        'schedule': crontab(minute=0),  # Каждый час в 0 минут
        'options': {
            'queue': 'vk_monitoring',
            'priority': 5
        }
    },
    'vk-carousel-validate-tokens': {
        'task': 'vk_carousel.validate_tokens',
        'schedule': crontab(minute=0, hour='*/6'),  # Каждые 6 часов
        'options': {
            'queue': 'vk_monitoring',
            'priority': 3
        }
    },
    'vk-carousel-optimize-frequency': {
        'task': 'vk_carousel.optimize_frequency',
        'schedule': crontab(minute=0, hour=2),  # Каждый день в 2:00
        'options': {
            'queue': 'vk_monitoring',
            'priority': 2
        }
    },
    'vk-carousel-status': {
        'task': 'vk_carousel.get_status',
        'schedule': crontab(minute='*/15'),  # Каждые 15 минут
        'options': {
            'queue': 'vk_monitoring',
            'priority': 1
        }
    }
})

# Настройка очередей
app.conf.task_routes = {
    'vk_carousel.*': {'queue': 'vk_monitoring'},
}

# Настройка ограничений
app.conf.task_annotations = {
    'vk_carousel.scan_next_region': {'rate_limit': '1/m'},  # 1 задача в минуту
    'vk_carousel.validate_tokens': {'rate_limit': '1/h'},   # 1 задача в час
    'vk_carousel.optimize_frequency': {'rate_limit': '1/d'}, # 1 задача в день
    'vk_carousel.get_status': {'rate_limit': '4/h'},        # 4 задачи в час
}
