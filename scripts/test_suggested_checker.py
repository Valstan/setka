#!/usr/bin/env python3
"""
Тест проверки предложенных постов

Проверяет все главные группы регионов на наличие предложенных постов.
"""
import sys
import os
import asyncio
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.connection import AsyncSessionLocal
from database.models import Region
from modules.notifications.vk_suggested_checker import VKSuggestedChecker
from modules.notifications.storage import NotificationsStorage
from config.runtime import VK_TOKENS
from sqlalchemy import select

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def test_suggested_checker():
    """Тест проверки предложенных постов"""
    logger.info("=" * 80)
    logger.info("ТЕСТ ПРОВЕРКИ ПРЕДЛОЖЕННЫХ ПОСТОВ")
    logger.info("=" * 80)
    
    # Получаем все регионы с главными группами (независимо от статуса активности)
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Region).where(
                Region.vk_group_id.isnot(None)
            )
        )
        regions = list(result.scalars())
        
        if not regions:
            logger.error("❌ Регионы с VK группами не найдены")
            return False
        
        logger.info(f"✅ Найдено регионов с VK группами: {len(regions)}\n")
        
        # Подготавливаем данные
        region_groups = [
            {
                'region_id': r.id,
                'region_name': r.name,
                'region_code': r.code,
                'vk_group_id': r.vk_group_id
            }
            for r in regions
        ]
        
        # Показываем список групп
        logger.info("Проверяемые группы:")
        for rg in region_groups:
            logger.info(f"  {rg['region_name']}: https://vk.com/club{abs(rg['vk_group_id'])}")
        
        logger.info("")
        
        # Проверяем предложенные посты
        vk_token = VK_TOKENS.get("VALSTAN")
        if not vk_token:
            logger.error("❌ VK token не найден")
            return False
        
        checker = VKSuggestedChecker(vk_token)
        notifications = await checker.check_all_region_groups(region_groups)
        
        logger.info("")
        logger.info("=" * 80)
        logger.info("РЕЗУЛЬТАТЫ")
        logger.info("=" * 80)
        
        if not notifications:
            logger.info("✅ Нет предложенных постов ни в одной группе!")
        else:
            logger.info(f"📬 Найдено групп с предложенными постами: {len(notifications)}\n")
            
            for notif in notifications:
                logger.info(f"📍 {notif['region_name']}")
                logger.info(f"   Постов: {notif['suggested_count']}")
                logger.info(f"   🔗 {notif['url']}")
                logger.info("")
        
        # Сохраняем в Redis
        logger.info("Сохранение в Redis...")
        storage = NotificationsStorage()
        storage.save_notifications(notifications)
        logger.info("✅ Сохранено!")
        
        # Проверяем, что сохранилось
        saved = storage.get_notifications_with_timestamp()
        logger.info(f"✅ В Redis: {len(saved['notifications'])} уведомлений")
        logger.info(f"   Timestamp: {saved['timestamp']}")
        
        logger.info("")
        logger.info("=" * 80)
        logger.info("🎯 Проверьте Dashboard: http://3931b3fe50ab.vps.myjino.ru/")
        logger.info("=" * 80)
        
        return True


async def main():
    """Главная функция"""
    try:
        success = await test_suggested_checker()
        return 0 if success else 1
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)

