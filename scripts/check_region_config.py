#!/usr/bin/env python3
"""Check RegionConfig data"""
import asyncio

from sqlalchemy import select

from database.connection import AsyncSessionLocal
from database.models import Community, Region
from database.models_extended import RegionConfig


async def main():
    async with AsyncSessionLocal() as session:
        # Regions
        regions = await session.execute(select(Region))
        print("=== Regions ===")
        for r in regions.scalars().all():
            print(
                f"  {r.code:20s} | {r.name:30s} | vk_group={r.vk_group_id} | active={r.is_active}"
            )

        # RegionConfigs
        configs = await session.execute(select(RegionConfig))
        print(f"\n=== RegionConfigs ({len(configs.scalars().all())}) ===")
        for c in configs.scalars().all():
            print(f"  {c.region_code:20s} | zagolovki={c.zagolovki}")

        # Check if test has config
        result = await session.execute(
            select(RegionConfig).where(RegionConfig.region_code == "test")
        )
        test_config = result.scalars().first()
        if not test_config:
            print("\n⚠️ NO RegionConfig for 'test' — creating one...")
            test_config = RegionConfig(
                region_code="test",
                zagolovki={
                    "novost": "📰 Новости",
                    "kultura": "🎭 Культура",
                    "sport": "⚽ Спорт",
                    "reklama": "📢 Объявления",
                },
                heshteg={
                    "novost": "новости",
                    "kultura": "культура",
                    "sport": "спорт",
                    "reklama": "реклама",
                },
                heshteg_local={"raicentr": "тест"},
                black_id=[],
                delete_msg_blacklist=[],
                region_words={},
                time_old_post={"hard": 86400, "medium": 172800},
                text_post_maxsize_simbols=4096,
                setka_regim_repost=False,
                sosed="",
            )
            session.add(test_config)
            await session.commit()
            print("✅ Created RegionConfig for 'test'")

        # Check communities for test region
        test_region = await session.execute(select(Region).where(Region.code == "test"))
        test_region = test_region.scalars().first()
        if test_region:
            comms = await session.execute(
                select(Community).where(Community.region_id == test_region.id)
            )
            print(f"\n=== Communities for test region ({len(comms.scalars().all())}) ===")
            for c in comms.scalars().all():
                print(
                    f"  {c.name:40s} | category={c.category:20s} | "
                    f"active={c.is_active} | vk_id={c.vk_id}"
                )


asyncio.run(main())
