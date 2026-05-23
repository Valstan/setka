#!/usr/bin/env python3
"""
Migrate region configs from old_postopus to SETKA.
Populates region_configs table with zagolovki, heshteg, heshteg_local
for all regions based on old_postopus MongoDB data.

Run with: venv/bin/python scripts/migrate_region_configs.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio  # noqa: E402

from sqlalchemy import select  # noqa: E402

from database.connection import AsyncSessionLocal  # noqa: E402
from database.models import Region  # noqa: E402
from database.models_extended import RegionConfig  # noqa: E402

# ============================================================================
# OLD_POSTOPUS DATA — extracted from MongoDB postopus.config collection
# These are the exact configs used by old_postopus parser.py
# ============================================================================
REGION_DATA = {
    # МАЛМЫЖ
    "mi": {
        "zagolovki": {
            "novost": "Физическое развитие:",
            "kultura": "Развитие творческих способностей:",
            "sport": "Физическое развитие:",
            "reklama": "Объявления:",
            "admin": "Уважаемые земляки!",
            "union": "Объединённый дайджест:",
            "addons": "Дополнительно:",
        },
        "heshteg": {
            "novost": "новостиМалмыж",
            "kultura": "культураМалмыж",
            "sport": "спортМалмыж",
            "reklama": "рекламаМалмыж",
            "admin": "МалмыжАдм",
            "union": "МалмыжОбъединение",
            "addons": "МалмыжДоп",
        },
        "heshteg_local": {"raicentr": "малмыж"},
    },
    # ВЯТСКИЕ ПОЛЯНЫ
    "vp": {
        "zagolovki": {
            "novost": "Новости Вятских Полян:",
            "kultura": "Культура Вятских Полян:",
            "sport": "Спорт Вятских Полян:",
            "reklama": "Объявления Вятские Поляны:",
            "admin": "Администрация Вятские Поляны:",
            "union": "Объединённый дайджест:",
        },
        "heshteg": {
            "novost": "новостиВП",
            "kultura": "культураВП",
            "sport": "спортВП",
            "reklama": "рекламаВП",
        },
        "heshteg_local": {"raicentr": "вятскиеп"},
    },
    # УРЖУМ
    "ur": {
        "zagolovki": {
            "novost": "Новости Уржума:",
            "kultura": "Культура Уржума:",
            "sport": "Спорт Уржума:",
            "reklama": "Объявления Уржум:",
            "admin": "Администрация Уржум:",
            "union": "Объединённый дайджест:",
        },
        "heshteg": {
            "novost": "новостиУржум",
            "kultura": "культураУржум",
            "sport": "спортУржум",
            "reklama": "рекламаУржум",
        },
        "heshteg_local": {"raicentr": "уржум"},
    },
    # КИЛЬМЕЗЬ
    "klz": {
        "zagolovki": {
            "novost": "Новости Кильмези:",
            "kultura": "Культура Кильмези:",
            "sport": "Спорт Кильмези:",
            "reklama": "Объявления Кильмезь:",
            "admin": "Администрация Кильмезь:",
            "union": "Объединённый дайджест:",
        },
        "heshteg": {
            "novost": "новостиКильмезь",
            "kultura": "культураКильмезь",
            "sport": "спортКильмезь",
            "reklama": "рекламаКильмезь",
        },
        "heshteg_local": {"raicentr": "кильмезь"},
    },
    # НОЛИНСК
    "nolinsk": {
        "zagolovki": {
            "novost": "Новости Нолинска:",
            "kultura": "Культура Нолинска:",
            "sport": "Спорт Нолинска:",
            "reklama": "Объявления Нолинск:",
            "admin": "Администрация Нолинск:",
            "union": "Объединённый дайджест:",
        },
        "heshteg": {
            "novost": "новостиНолинск",
            "kultura": "культураНолинск",
            "sport": "спортНолинск",
            "reklama": "рекламаНолинск",
        },
        "heshteg_local": {"raicentr": "нолинск"},
    },
    # ПИЖАНКА
    "pizhanka": {
        "zagolovki": {
            "novost": "Новости Пижанки:",
            "kultura": "Культура Пижанки:",
            "sport": "Спорт Пижанки:",
            "reklama": "Объявления Пижанка:",
            "admin": "Администрация Пижанка:",
        },
        "heshteg": {
            "novost": "новостиПижанка",
            "kultura": "культураПижанка",
            "sport": "спортПижанка",
            "reklama": "рекламаПижанка",
        },
        "heshteg_local": {"raicentr": "пижанка"},
    },
    # АРБАЖ
    "arbazh": {
        "zagolovki": {
            "novost": "Новости Арбажа:",
            "kultura": "Культура Арбажа:",
            "sport": "Спорт Арбажа:",
            "reklama": "Объявления Арбаж:",
            "admin": "Администрация Арбаж:",
        },
        "heshteg": {
            "novost": "новостиАрбаж",
            "kultura": "культураАрбаж",
            "sport": "спортАрбаж",
            "reklama": "рекламаАрбаж",
        },
        "heshteg_local": {"raicentr": "арбаж"},
    },
    # СОВЕТСК
    "sovetsk": {
        "zagolovki": {
            "novost": "Новости Советска:",
            "kultura": "Культура Советска:",
            "sport": "Спорт Советска:",
            "reklama": "Объявления Советск:",
            "admin": "Администрация Советск:",
        },
        "heshteg": {
            "novost": "новостиСоветск",
            "kultura": "культураСоветск",
            "sport": "спортСоветск",
            "reklama": "рекламаСоветск",
        },
        "heshteg_local": {"raicentr": "советск"},
    },
    # ЛЕБЯЖЬЕ
    "leb": {
        "zagolovki": {
            "novost": "Новости Лебяжья:",
            "kultura": "Культура Лебяжья:",
            "sport": "Спортивные новости Лебяжье:",
            "reklama": "Объявления Лебяжье:",
            "admin": "Администрация Лебяжье:",
        },
        "heshteg": {
            "novost": "новостиЛебяжье",
            "kultura": "культураЛебяжье",
            "sport": "спортЛебяжье",
            "reklama": "рекламаЛебяжье",
        },
        "heshteg_local": {"raicentr": "лебяжье"},
    },
    # БАЛТАСИ
    "bal": {
        "zagolovki": {
            "novost": "Новости Балтасей:",
            "kultura": "Культура Балтасей:",
            "sport": "Спорт Балтаси:",
            "reklama": "Объявления Балтаси:",
            "admin": "Администрация Балтаси:",
        },
        "heshteg": {
            "novost": "новостиБалтаси",
            "kultura": "культураБалтаси",
            "sport": "спортБалтаси",
            "reklama": "рекламаБалтаси",
        },
        "heshteg_local": {"raicentr": "балтаси"},
    },
    # КУКМОР
    "kukmor": {
        "zagolovki": {
            "novost": "Новости Кукмора:",
            "kultura": "Культура Кукмора:",
            "sport": "Спорт Кукмора:",
            "reklama": "Объявления Кукмор:",
            "admin": "Администрация Кукмор:",
        },
        "heshteg": {
            "novost": "новостиКукмор",
            "kultura": "культураКукмор",
            "sport": "спортКукмор",
            "reklama": "рекламаКукмор",
        },
        "heshteg_local": {"raicentr": "кукмор"},
    },
    # НЕМА
    "nema": {
        "zagolovki": {
            "novost": "Новости Немы:",
            "kultura": "Культура Немы:",
            "sport": "Спорт Немы:",
            "reklama": "Объявления Нема:",
            "admin": "Администрация Нема:",
        },
        "heshteg": {
            "novost": "новостиНема",
            "kultura": "культураНема",
            "sport": "спортНема",
            "reklama": "рекламаНема",
        },
        "heshteg_local": {"raicentr": "нема"},
    },
    # ВЕРХОШИЖЕМЬЕ
    "verhoshizhem": {
        "zagolovki": {
            "novost": "Новости Верхошижемья:",
            "kultura": "Культура Верхошижемья:",
            "sport": "Спорт Верхошижемья:",
            "reklama": "Объявления Верхошижемье:",
            "admin": "Администрация Верхошижемье:",
        },
        "heshteg": {
            "novost": "новостиВерхошижемье",
            "kultura": "культураВерхошижемье",
            "sport": "спортВерхошижемье",
            "reklama": "рекламаВерхошижемье",
        },
        "heshteg_local": {"raicentr": "верхошижемье"},
    },
    # ТЕСТ
    "test": {
        "zagolovki": {
            "novost": "📰 Новости",
            "kultura": "🎭 Культура",
            "sport": "⚽ Спорт",
            "reklama": "📢 Объявления",
            "admin": "🏛 Администрация",
        },
        "heshteg": {
            "novost": "новости",
            "kultura": "культура",
            "sport": "спорт",
            "reklama": "реклама",
        },
        "heshteg_local": {"raicentr": "тест"},
    },
}


async def main():
    async with AsyncSessionLocal() as session:
        regions_result = await session.execute(select(Region))
        regions = regions_result.scalars().all()
        region_codes = {r.code for r in regions}
        print(f"Found {len(regions)} regions: {sorted(region_codes)}")

        created = 0
        updated = 0

        for code, data in REGION_DATA.items():
            if code not in region_codes:
                print(f"  ⏭ Skipping {code} — not in DB")
                continue

            result = await session.execute(
                select(RegionConfig).where(RegionConfig.region_code == code)
            )
            existing = result.scalars().first()

            if existing:
                existing.zagolovki = data["zagolovki"]
                existing.heshteg = data["heshteg"]
                existing.heshteg_local = data["heshteg_local"]
                updated += 1
                print(f"  ✏️  Updated: {code}")
            else:
                new_config = RegionConfig(
                    region_code=code,
                    zagolovki=data["zagolovki"],
                    heshteg=data["heshteg"],
                    heshteg_local=data["heshteg_local"],
                )
                session.add(new_config)
                created += 1
                print(f"  ➕ Created: {code}")

        await session.commit()
        print(f"\n✅ Migration complete: {created} created, {updated} updated")


if __name__ == "__main__":
    asyncio.run(main())
