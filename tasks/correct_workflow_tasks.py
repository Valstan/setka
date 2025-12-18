"""
Correct Workflow Celery Tasks

Правильная логика работы SETKA:
1. Получить текущую тематику по расписанию
2. Найти сообщества этой тематики для региона
3. Собрать посты из этих сообществ за последние 3 дня
4. Применить фильтры системы
5. Создать дайджест из подходящих постов
6. Опубликовать в главную группу региона
"""
import logging
import asyncio
from datetime import datetime
from typing import Dict, Any
import pytz

from celery import Task
from celery_app import app
from modules.correct_workflow import correct_workflow_manager

logger = logging.getLogger(__name__)


@app.task(bind=True, name='tasks.correct_workflow_tasks.run_correct_workflow')
def run_correct_workflow(self: Task):
    """
    Запуск правильного workflow для всех регионов
    
    Выполняется каждый час с 7:00 до 22:00 MSK
    Использует правильную логику: тематика → сообщества → посты → фильтрация → дайджест → публикация
    """
    logger.info("="*80)
    logger.info("🚀 Starting Correct Workflow")
    logger.info("="*80)
    
    try:
        # Проверка рабочих часов (7:00 - 22:00 MSK)
        moscow_tz = pytz.timezone('Europe/Moscow')
        now_moscow = datetime.now(moscow_tz)
        current_hour = now_moscow.hour
        
        work_hours_start = 7
        work_hours_end = 22
        
        if not (work_hours_start <= current_hour <= work_hours_end):
            logger.info(f"😴 Outside work hours: {current_hour}:00 MSK (work: {work_hours_start}:00-{work_hours_end}:00)")
            return {
                'success': False,
                'reason': 'outside_work_hours',
                'current_hour': current_hour,
                'work_hours': f"{work_hours_start}:00-{work_hours_end}:00 MSK",
                'timestamp': now_moscow.isoformat()
            }
        
        logger.info(f"✅ Inside work hours: {current_hour}:00 MSK")
        
        # Запускаем async функцию в event loop
        result = asyncio.run(correct_workflow_manager.process_all_regions_by_schedule())
        
        logger.info("="*80)
        logger.info("📊 CORRECT WORKFLOW COMPLETE")
        logger.info("="*80)
        
        if result.get('success'):
            logger.info(f"✅ Processed {result.get('total_regions', 0)} regions")
            logger.info(f"✅ Successful: {result.get('successful', 0)}")
            logger.info(f"❌ Failed: {result.get('failed', 0)}")
        else:
            logger.error(f"❌ Workflow failed: {result.get('error', 'Unknown error')}")
        
        return result
        
    except Exception as e:
        logger.error(f"❌ Correct workflow failed: {e}", exc_info=True)
        return {
            'success': False,
            'error': str(e),
            'timestamp': datetime.now().isoformat()
        }


@app.task(bind=True, name='tasks.correct_workflow_tasks.test_single_region')
def test_single_region(self: Task, region_code: str = "test"):
    """
    Тестовая задача для одного региона
    
    Args:
        region_code: Код региона для тестирования
    """
    logger.info("="*60)
    logger.info(f"🧪 Testing Correct Workflow for region: {region_code}")
    logger.info("="*60)
    
    try:
        # Запускаем async функцию в event loop
        result = asyncio.run(correct_workflow_manager.process_region_by_schedule(region_code))
        
        logger.info("="*60)
        logger.info("📊 SINGLE REGION TEST COMPLETE")
        logger.info("="*60)
        
        if result.get('success'):
            logger.info(f"✅ Region: {result.get('region', 'Unknown')}")
            logger.info(f"✅ Topic: {result.get('topic', 'Unknown')}")
            logger.info(f"✅ Communities: {result.get('communities_count', 0)}")
            logger.info(f"✅ Posts collected: {result.get('posts_collected', 0)}")
            logger.info(f"✅ Posts approved: {result.get('posts_approved', 0)}")
            logger.info(f"✅ Posts rejected: {result.get('posts_rejected', 0)}")
            logger.info(f"✅ Digest length: {result.get('digest_length', 0)} characters")
            logger.info(f"✅ Published: {result.get('published', False)}")
        else:
            logger.error(f"❌ Test failed: {result.get('error', 'Unknown error')}")
        
        return result
        
    except Exception as e:
        logger.error(f"❌ Single region test failed: {e}", exc_info=True)
        return {
            'success': False,
            'error': str(e),
            'region_code': region_code,
            'timestamp': datetime.now().isoformat()
        }


if __name__ == "__main__":
    # Простой тест
    print("Testing correct workflow task...")
    result = test_single_region("test")
    print(f"Result: {result}")
