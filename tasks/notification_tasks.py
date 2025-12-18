"""
Celery tasks for VK notifications monitoring

Автоматическая проверка:
1. Предложенных постов
2. Непрочитанных сообщений

График: Раз в час
"""
import logging
import asyncio
from typing import List, Dict, Any
from celery import Task

from celery_app import app
from config.config_secure import VK_TOKENS, TELEGRAM_TOKENS, TELEGRAM_ALERT_CHAT_ID, SERVER
from modules.notifications.unified_checker import UnifiedNotificationsChecker
from database.connection import AsyncSessionLocal
from database.models import Region
from sqlalchemy import select

logger = logging.getLogger(__name__)


@app.task(bind=True, name='tasks.notification_tasks.check_vk_notifications')
def check_vk_notifications(self: Task):
    """
    Celery task: Проверка VK уведомлений (suggested posts + unread messages)
    
    Запускается каждый час (см. celery_app.py beat_schedule)
    Работает только с 8:00 до 22:00 по московскому времени
    """
    # Проверить рабочие часы (8:00 - 22:00 по Москве)
    from datetime import datetime
    import pytz
    
    moscow_tz = pytz.timezone('Europe/Moscow')
    now_moscow = datetime.now(moscow_tz)
    current_hour = now_moscow.hour
    
    # Рабочие часы: 8:00 - 22:00
    WORK_HOURS_START = 8
    WORK_HOURS_END = 22
    
    if not (WORK_HOURS_START <= current_hour < WORK_HOURS_END):
        logger.info(f"😴 Outside work hours (current: {current_hour}:00 MSK, work: {WORK_HOURS_START}:00-{WORK_HOURS_END}:00)")
        logger.info("⏸️  Skipping VK notifications check (server resting)")
        return {
            'skipped': True,
            'reason': f'Outside work hours ({current_hour}:00 MSK)',
            'work_hours': f'{WORK_HOURS_START}:00-{WORK_HOURS_END}:00 MSK',
            'next_check': f'Next check at {WORK_HOURS_START}:00 MSK'
        }
    
    logger.info("="*60)
    logger.info(f"🔔 Starting VK notifications check (MSK: {current_hour}:00)...")
    logger.info("="*60)
    
    try:
        # Запускаем async функцию в event loop
        result = asyncio.run(_check_vk_notifications_async())
        
        logger.info("="*60)
        logger.info(f"✅ VK notifications check complete: {result['total_count']} total")
        logger.info("="*60)
        
        return result
        
    except Exception as e:
        logger.error(f"❌ Error in check_vk_notifications task: {e}", exc_info=True)
        raise


async def _check_vk_notifications_async():
    """Async функция для проверки уведомлений"""
    
    # Получить VK токен
    vk_token = VK_TOKENS.get("VALSTAN")
    if not vk_token:
        logger.error("VK token not found!")
        return {'error': 'No VK token'}
    
    # Инициализировать checker
    checker = UnifiedNotificationsChecker(vk_token)
    
    # Получить список главных групп регионов из БД
    region_groups = await _get_region_groups()
    
    if not region_groups:
        logger.warning("No region groups found in database")
        return {'error': 'No region groups'}
    
    logger.info(f"Checking {len(region_groups)} region groups...")
    
    # Проверить все уведомления
    result = await checker.check_all(region_groups)
    
    # Если есть уведомления, отправить в Telegram
    if result['total_count'] > 0:
        logger.info(f"📬 Found {result['total_count']} notifications, sending to Telegram...")
        
        # Получить Telegram токен и chat_id
        telegram_token = TELEGRAM_TOKENS.get("VALSTANBOT")
        chat_id = TELEGRAM_ALERT_CHAT_ID
        
        if telegram_token and chat_id:
            # URL кабинета уведомлений
            dashboard_url = f"https://{SERVER['domain']}/notifications"
            
            # Отправить уведомление
            await checker.send_telegram_notification(
                bot_token=telegram_token,
                chat_id=chat_id,
                notifications_data=result,
                dashboard_url=dashboard_url
            )
        else:
            logger.warning("Telegram credentials not configured, skipping notification")
    else:
        logger.info("ℹ️  No notifications found")
    
    return result


async def _get_region_groups() -> List[Dict[str, Any]]:
    """
    Получить список главных VK групп всех регионов
    
    Returns:
        List dict с информацией о группах
    """
    async with AsyncSessionLocal() as session:
        # Получить все регионы с VK группами (независимо от статуса активности)
        result = await session.execute(
            select(Region).where(
                Region.vk_group_id != None
            )
        )
        
        regions = result.scalars().all()
        
        region_groups = []
        for region in regions:
            region_groups.append({
                'region_id': region.id,
                'region_name': region.name,
                'region_code': region.code,
                'vk_group_id': region.vk_group_id
            })
        
        logger.info(f"Found {len(region_groups)} region groups in database (all regions with VK groups)")
        
        return region_groups


# Можно вызвать вручную для тестирования
def test_notifications_check():
    """Тестовая функция для ручного запуска"""
    return check_vk_notifications()


if __name__ == "__main__":
    # Тест
    print("Testing VK Notifications Check...")
    print("="*60)
    
    result = test_notifications_check()
    print(f"\nResult: {result}")
    print("="*60)
    print("✅ Test complete")

