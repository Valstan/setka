"""
Test-Info Celery Tasks
Задачи для региона "Тест-Инфо" с круглосуточным расписанием
"""
import logging
from datetime import datetime
from typing import Dict, Any
import pytz

from celery_app import app
from modules.test_info_scheduler import test_info_scheduler
from utils.timezone import now_moscow, is_work_hours_for_region
from config.runtime import VK_MAIN_TOKENS

logger = logging.getLogger(__name__)


@app.task(bind=True, name='tasks.test_info_tasks.execute_test_info_schedule')
def execute_test_info_schedule(self):
    """
    Выполнить расписание для Тест-Инфо
    
    Запускается каждые 5 минут круглосуточно
    Перебирает темы по кругу и публикует дайджесты
    """
    logger.info("="*80)
    logger.info("🌙 Starting Test-Info Scheduled Task")
    logger.info("="*80)
    
    try:
        current_time = now_moscow()
        current_hour = current_time.hour
        
        # Проверяем, что это Тест-Инфо (должен работать круглосуточно)
        region_name = "Тест-Инфо"
        is_work_hours = is_work_hours_for_region(region_name, 7, 22)
        
        if not is_work_hours:
            logger.error(f"❌ CRITICAL ERROR: Test-Info should work 24/7 but work hours check failed!")
            return {
                'success': False,
                'reason': 'work_hours_check_failed',
                'region': region_name,
                'current_hour': current_hour,
                'timestamp': current_time.isoformat()
            }
        
        logger.info(f"🌙 Test-Info works 24/7 (time: {current_hour}:00 MSK)")
        
        # Проверяем, нужно ли выполнять задачу сейчас
        if not test_info_scheduler.should_execute_now():
            time_until_next = test_info_scheduler.get_time_until_next_execution()
            logger.info(f"⏳ Not time to execute yet. Time until next: {time_until_next}")
            
            return {
                'success': True,
                'reason': 'not_time_to_execute',
                'time_until_next': str(time_until_next) if time_until_next else None,
                'current_topic': test_info_scheduler.get_current_topic().value,
                'timestamp': current_time.isoformat()
            }
        
        # Выполняем задачу
        logger.info(f"🚀 Executing Test-Info scheduled task...")
        
        # Получаем VK токен
        valstan_token = VK_MAIN_TOKENS.get("VALSTAN", {}).get("token")
        if not valstan_token:
            logger.error("❌ VALSTAN token not found")
            return {
                'success': False,
                'error': 'VALSTAN token not found',
                'timestamp': current_time.isoformat()
            }
        
        # Запускаем async функцию
        import asyncio
        result = asyncio.run(test_info_scheduler.execute_scheduled_task(valstan_token))
        
        logger.info("="*80)
        logger.info("✅ Test-Info Scheduled Task Completed")
        logger.info("="*80)
        
        return result
        
    except Exception as e:
        logger.error(f"❌ Error in Test-Info scheduled task: {e}", exc_info=True)
        return {
            'success': False,
            'error': str(e),
            'region': region_name,
            'timestamp': current_time.isoformat()
        }


@app.task(bind=True, name='tasks.test_info_tasks.get_test_info_status')
def get_test_info_status(self):
    """
    Получить статус расписания Тест-Инфо
    
    Возвращает информацию о текущей теме, следующем выполнении и истории
    """
    try:
        status = test_info_scheduler.get_schedule_status()
        history = test_info_scheduler.get_execution_history(10)
        
        return {
            'success': True,
            'status': status,
            'recent_history': history,
            'timestamp': now_moscow().isoformat()
        }
        
    except Exception as e:
        logger.error(f"❌ Error getting Test-Info status: {e}")
        return {
            'success': False,
            'error': str(e),
            'timestamp': now_moscow().isoformat()
        }


@app.task(bind=True, name='tasks.test_info_tasks.test_test_info_workflow')
def test_test_info_workflow(self):
    """
    Тестовая задача для проверки работы Тест-Инфо
    
    Можно запускать вручную для тестирования
    """
    logger.info("🧪 Testing Test-Info Workflow")
    
    try:
        current_time = now_moscow()
        # Получаем VK токен
        valstan_token = VK_MAIN_TOKENS.get("VALSTAN", {}).get("token")
        if not valstan_token:
            logger.error("❌ VALSTAN token not found")
            return {
                'success': False,
                'error': 'VALSTAN token not found',
                'timestamp': current_time.isoformat()
            }
        
        # Принудительно выполняем задачу (игнорируем время)
        import asyncio
        result = asyncio.run(test_info_scheduler.execute_scheduled_task(valstan_token))
        
        logger.info(f"✅ Test completed: {result['success']}")
        
        return {
            'success': True,
            'test_result': result,
            'timestamp': now_moscow().isoformat()
        }
        
    except Exception as e:
        logger.error(f"❌ Test failed: {e}")
        return {
            'success': False,
            'error': str(e),
            'timestamp': now_moscow().isoformat()
        }


if __name__ == "__main__":
    # Тестирование задач
    print("🧪 Testing Test-Info Celery Tasks")
    print("=" * 50)
    
    # Тестируем получение статуса
    print("1. Testing status retrieval...")
    status_result = get_test_info_status()
    print(f"Status success: {status_result['success']}")
    if status_result['success']:
        status = status_result['status']
        print(f"Current topic: {status['current_topic']}")
        print(f"Next topic: {status['next_topic']}")
        print(f"Execution count: {status['execution_count']}")
    
    # Тестируем выполнение задачи
    print("\n2. Testing task execution...")
    execution_result = execute_test_info_schedule()
    print(f"Execution success: {execution_result['success']}")
    if execution_result['success']:
        print(f"Topic: {execution_result.get('topic', 'N/A')}")
        print(f"Posts collected: {execution_result.get('posts_collected', 0)}")
        print(f"Posts approved: {execution_result.get('posts_approved', 0)}")
    
    print("\n✅ Test completed!")
