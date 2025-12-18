#!/usr/bin/env python3
"""
Синхронизация настроек регионов с базой данных

Этот скрипт обновляет настройки регионов в базе данных
на основе централизованной конфигурации.
"""
import sys
import os
import asyncio
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.connection import get_db_session_context
from database.models import Region
from sqlalchemy import select, update
from modules.region_config import REGIONS_CONFIG

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def sync_region_settings():
    """Синхронизировать настройки регионов с базой данных"""
    async with get_db_session_context() as session:
        updated_count = 0
        created_count = 0
        
        for region_code, config in REGIONS_CONFIG.items():
            logger.info(f"Обрабатываем регион: {region_code}")
            
            # Проверяем, существует ли регион в БД
            result = await session.execute(
                select(Region).where(Region.code == region_code)
            )
            region = result.scalar_one_or_none()
            
            if region:
                # Обновляем существующий регион
                logger.info(f"  Обновляем существующий регион: {region.name}")
                
                await session.execute(
                    update(Region)
                    .where(Region.id == region.id)
                    .values(
                        name=config.name,
                        vk_group_id=config.main_group_id,
                        telegram_channel=config.telegram_channel,
                        neighbors=",".join(config.neighbors) if config.neighbors else None,
                        local_hashtags=",".join(config.local_hashtags) if config.local_hashtags else None,
                        is_active=config.is_active
                    )
                )
                updated_count += 1
            else:
                # Создаем новый регион
                logger.info(f"  Создаем новый регион: {config.name}")
                
                new_region = Region(
                    code=config.code,
                    name=config.name,
                    vk_group_id=config.main_group_id,
                    telegram_channel=config.telegram_channel,
                    neighbors=",".join(config.neighbors) if config.neighbors else None,
                    local_hashtags=",".join(config.local_hashtags) if config.local_hashtags else None,
                    is_active=config.is_active
                )
                
                session.add(new_region)
                created_count += 1
        
        await session.commit()
        
        logger.info(f"✅ Синхронизация завершена!")
        logger.info(f"  Обновлено регионов: {updated_count}")
        logger.info(f"  Создано регионов: {created_count}")


async def show_region_settings():
    """Показать текущие настройки регионов"""
    async with get_db_session_context() as session:
        result = await session.execute(select(Region).order_by(Region.code))
        regions = result.scalars().all()
        
        logger.info("📋 ТЕКУЩИЕ НАСТРОЙКИ РЕГИОНОВ В БД:")
        logger.info("=" * 80)
        
        for region in regions:
            logger.info(f"📍 {region.code.upper()}: {region.name}")
            logger.info(f"   ID: {region.id}")
            logger.info(f"   Главная группа: {region.vk_group_id}")
            logger.info(f"   Telegram: {region.telegram_channel}")
            logger.info(f"   Активен: {region.is_active}")
            logger.info(f"   Соседи: {region.neighbors}")
            logger.info(f"   Хештеги: {region.local_hashtags}")
            logger.info()


async def main():
    """Главная функция"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Синхронизация настроек регионов')
    parser.add_argument('--show', action='store_true', help='Показать текущие настройки')
    parser.add_argument('--sync', action='store_true', help='Синхронизировать настройки')
    
    args = parser.parse_args()
    
    if args.show:
        await show_region_settings()
    elif args.sync:
        await sync_region_settings()
    else:
        logger.error("Укажите --show или --sync")
        return 1
    
    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
