#!/usr/bin/env python3
"""
Поиск главных VK групп для регионов

Ищет группы с названиями типа:
- "Малмыж Инфо"
- "Нолинск Инфо"
- "{Регион} Инфо"

И обновляет vk_group_id в таблице regions
"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import vk_api  # noqa: E402
from sqlalchemy import select, update  # noqa: E402

from config.runtime import VK_TOKENS  # noqa: E402
from database.connection import AsyncSessionLocal  # noqa: E402
from database.models import Region  # noqa: E402

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def search_vk_group(vk, region_name: str) -> dict:
    """
    Поиск VK группы по названию региона

    Args:
        vk: VK API session
        region_name: Название региона (например "МАЛМЫЖ - ИНФО")

    Returns:
        Dict с информацией о группе или None
    """
    # Извлекаем название региона без " - ИНФО"
    region_base = region_name.replace(" - ИНФО", "").strip()

    # Варианты поиска
    search_queries = [
        f"{region_base} Инфо",
        f"{region_base} - Инфо",
        f"{region_base.title()} Инфо",
        region_base,
    ]

    logger.info(f"Ищем группу для региона: {region_name}")

    for query in search_queries:
        try:
            logger.info(f"  Поиск по запросу: '{query}'")
            results = vk.groups.search(q=query, count=10)

            if results and "items" in results and results["items"]:
                for group in results["items"]:
                    group_name = group.get("name", "")
                    group_id = group.get("id")
                    screen_name = group.get("screen_name", "")

                    # Проверяем, что это похоже на нужную группу
                    if "инфо" in group_name.lower() and region_base.lower() in group_name.lower():
                        logger.info(f"  ✅ Найдена: {group_name} (ID: -{group_id})")
                        return {
                            "id": -group_id,  # Отрицательное для групп
                            "name": group_name,
                            "screen_name": screen_name,
                            "url": f"https://vk.com/{screen_name}",
                        }
        except Exception as e:
            logger.warning(f"  Ошибка поиска по '{query}': {e}")
            continue

    logger.warning(f"  ⚠️  Группа не найдена для региона {region_name}")
    return None


async def find_and_update_region_groups():
    """Найти и обновить VK группы для всех регионов"""
    logger.info("=" * 80)
    logger.info("ПОИСК ГЛАВНЫХ VK ГРУПП ДЛЯ РЕГИОНОВ")
    logger.info("=" * 80)

    # Инициализация VK API
    vk_token = VK_TOKENS.get("VALSTAN")
    if not vk_token:
        logger.error("❌ VK токен VALSTAN не найден!")
        return False

    try:
        vk_session = vk_api.VkApi(token=vk_token)
        vk = vk_session.get_api()
        logger.info("✅ VK API инициализирован")
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации VK API: {e}")
        return False

    # Получаем все регионы из БД
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Region).order_by(Region.name))
        regions = list(result.scalars())

        logger.info(f"\n📊 Найдено регионов в БД: {len(regions)}\n")

        found_groups = []
        not_found = []

        for region in regions:
            logger.info(f"Регион: {region.name} (code: {region.code})")

            # Проверяем, есть ли уже группа
            if region.vk_group_id:
                logger.info(f"  ℹ️  Уже настроена группа: {region.vk_group_id}")
                found_groups.append(region)
                continue

            # Ищем группу
            group_info = search_vk_group(vk, region.name)

            if group_info:
                # Обновляем БД
                await session.execute(
                    update(Region)
                    .where(Region.id == region.id)
                    .values(vk_group_id=group_info["id"])
                )

                region.vk_group_id = group_info["id"]
                found_groups.append(region)

                logger.info(f"  ✅ Обновлено в БД: vk_group_id = {group_info['id']}")
                logger.info(f"  🔗 URL: {group_info['url']}")
            else:
                not_found.append(region)

            logger.info("")

        # Сохраняем изменения
        await session.commit()

        # Итоги
        logger.info("=" * 80)
        logger.info("РЕЗУЛЬТАТЫ ПОИСКА")
        logger.info("=" * 80)
        logger.info(f"✅ Найдено групп: {len(found_groups)}/{len(regions)}")

        if found_groups:
            logger.info("\nГруппы найдены для:")
            for region in found_groups:
                logger.info(f"  ✅ {region.name} → https://vk.com/club{abs(region.vk_group_id)}")

        if not_found:
            logger.info("\n⚠️  Группы НЕ найдены для:")
            for region in not_found:
                logger.info(f"  ❌ {region.name} (code: {region.code})")
                logger.info("     Подскажите VK ID или screen_name группы")

        logger.info("=" * 80)

        return True


async def main():
    """Главная функция"""
    try:
        success = await find_and_update_region_groups()
        return 0 if success else 1
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
