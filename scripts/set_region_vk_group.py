#!/usr/bin/env python3
"""
Установить VK group ID для региона вручную

Usage:
    python scripts/set_region_vk_group.py --region dran --group-id -123456789
    python scripts/set_region_vk_group.py --region dran --screen-name dran_info
"""
import argparse
import asyncio
import logging
import sys

import vk_api
from sqlalchemy import select, update

from config.runtime import VK_TOKENS
from database.connection import AsyncSessionLocal
from database.models import Region

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


async def set_vk_group_id(region_code: str, vk_group_id: int = None, screen_name: str = None):
    """
    Установить VK group ID для региона

    Args:
        region_code: Код региона (например, 'dran')
        vk_group_id: ID группы VK (отрицательное число)
        screen_name: Screen name группы (например, 'dran_info')
    """
    if not vk_group_id and not screen_name:
        logger.error("❌ Нужно указать либо --group-id, либо --screen-name")
        return False

    # Если указан screen_name, получаем ID через VK API
    if screen_name and not vk_group_id:
        vk_token = VK_TOKENS.get("VALSTAN")
        if not vk_token:
            logger.error("❌ VK токен не найден")
            return False

        try:
            vk_session = vk_api.VkApi(token=vk_token)
            vk = vk_session.get_api()

            # Получаем информацию о группе
            groups = vk.groups.getById(group_id=screen_name)
            if groups:
                group = groups[0]
                vk_group_id = -group["id"]  # Отрицательное для групп
                logger.info(f"✅ Найдена группа: {group['name']} (ID: {vk_group_id})")
            else:
                logger.error(f"❌ Группа с screen_name '{screen_name}' не найдена")
                return False
        except Exception as e:
            logger.error(f"❌ Ошибка получения информации о группе: {e}")
            return False

    # Обновляем БД
    async with AsyncSessionLocal() as session:
        # Проверяем, существует ли регион
        result = await session.execute(select(Region).where(Region.code == region_code))
        region = result.scalar_one_or_none()

        if not region:
            logger.error(f"❌ Регион с кодом '{region_code}' не найден")
            return False

        logger.info(f"Регион: {region.name} (code: {region.code})")
        logger.info(f"Старый vk_group_id: {region.vk_group_id}")
        logger.info(f"Новый vk_group_id: {vk_group_id}")

        # Обновляем
        await session.execute(
            update(Region).where(Region.id == region.id).values(vk_group_id=vk_group_id)
        )
        await session.commit()

        logger.info("✅ VK group ID обновлен!")
        logger.info(f"🔗 URL: https://vk.com/club{abs(vk_group_id)}")

        return True


async def main():
    """Главная функция"""
    parser = argparse.ArgumentParser(description="Установить VK group ID для региона")
    parser.add_argument("--region", required=True, help="Код региона (например, dran)")
    parser.add_argument("--group-id", type=int, help="ID группы VK (отрицательное число)")
    parser.add_argument("--screen-name", help="Screen name группы (например, dran_info)")

    args = parser.parse_args()

    try:
        success = await set_vk_group_id(args.region, args.group_id, args.screen_name)
        return 0 if success else 1
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
