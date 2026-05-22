#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Script to import data from old Postopus project to SETKA database
"""
import asyncio
import json
import os
import sys

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select

from database.connection import AsyncSessionLocal
from database.models import Community, Filter, Region, VKToken


def _collect_prefixed_env(prefix: str) -> dict[str, str]:
    """
    Collect env vars like VK_TOKEN_VALSTAN=... -> {"VALSTAN": "..."}.
    """
    out: dict[str, str] = {}
    for k, v in os.environ.items():
        if not k.startswith(prefix):
            continue
        if not v or len(v.strip()) < 10:
            continue
        name = k[len(prefix) :].strip("_")
        if not name:
            continue
        out[name.upper()] = v.strip()
    return out


async def import_regions():
    """Import regions from old project"""
    print("📍 Importing regions...")

    # Load old data
    with open("/home/valstan/SETKA/old_project_analysis/db_analysis.json", "r") as f:
        data = json.load(f)

    # Region mappings from old project
    region_mappings = {
        "mi": {"name": "МАЛМЫЖ - ИНФО", "telegram": "@malmig_info"},
        "nolinsk": {"name": "НОЛИНСК - ИНФО", "telegram": "@nolinsk_info"},
        "arbazh": {"name": "АРБАЖ - ИНФО", "telegram": "@arbazh_info"},
        "nema": {"name": "НЕМА - ИНФО", "telegram": "@nema_info"},
        "ur": {"name": "УРЖУМ - ИНФО", "telegram": "@"},
        "verhoshizhem": {"name": "ВЕРХОШИЖЕМЬЕ - ИНФО", "telegram": "@verhoshizhem_info"},
        "klz": {"name": "КИЛЬМЕЗЬ - ИНФО", "telegram": "@"},
        "pizhanka": {"name": "ПИЖАНКА - ИНФО", "telegram": "@pizhanka_info"},
        "kukmor": {"name": "КУКМОР - ИНФО", "telegram": "@kukmor_info"},
        "sovetsk": {"name": "СОВЕТСК - ИНФО", "telegram": "@sovetsk_info"},
        "vp": {"name": "ВЯТСКИЕ ПОЛЯНЫ - ИНФО", "telegram": "@"},
        "leb": {"name": "ЛЕБЯЖЬЕ - ИНФО", "telegram": "@lebyaje_info"},
        "dran": {"name": "ДРАН - ИНФО", "telegram": "@"},
        "bal": {"name": "БАЛТАСИ - ИНФО", "telegram": "@"},
        "afon": {"name": "АФОН - ИНФО", "telegram": "@"},
    }

    async with AsyncSessionLocal() as session:
        imported_count = 0

        for region_code, collections in data["collections"].items():
            if region_code in ["config", "malmigrus", "afon"]:
                continue  # Skip special collections

            if region_code not in region_mappings:
                print(f"  ⚠️  Unknown region: {region_code}")
                continue

            # Check if region already exists
            result = await session.execute(select(Region).where(Region.code == region_code))
            existing = result.scalar_one_or_none()

            if existing:
                print(f"  ⏭️  Region {region_code} already exists")
                continue

            # Get sample document to extract VK group ID and neighbors
            sample = collections.get("sample_doc", "")
            vk_group_id = None
            neighbors = None

            # Try to parse from sample
            if "post_group_vk" in sample:
                try:
                    import re

                    match = re.search(r"'post_group_vk': (-?\d+)", sample)
                    if match:
                        vk_group_id = int(match.group(1))
                except:
                    pass

            if "sosed" in sample:
                try:
                    import re

                    match = re.search(r"'sosed': '([^']+)'", sample)
                    if match:
                        neighbors = match.group(1)
                except:
                    pass

            # Create region
            region = Region(
                code=region_code,
                name=region_mappings[region_code]["name"],
                vk_group_id=vk_group_id,
                telegram_channel=region_mappings[region_code]["telegram"],
                neighbors=neighbors,
                is_active=True,
            )

            session.add(region)
            imported_count += 1
            print(f"  ✅ Imported: {region_code} - {region.name}")

        await session.commit()
        print(f"\n✅ Imported {imported_count} regions")


async def import_communities():
    """Import communities from old project"""
    print("\n📊 Importing communities...")

    # This will need to be done by parsing the sample_doc JSON more carefully
    # For now, we'll create a few sample communities for testing

    async with AsyncSessionLocal() as session:
        # Get Малмыж region
        result = await session.execute(select(Region).where(Region.code == "mi"))
        mi_region = result.scalar_one_or_none()

        if not mi_region:
            print("  ⚠️  Region 'mi' not found, skipping communities import")
            return

        # Sample communities (will need to extract from old DB properly)
        sample_communities = [
            {"vk_id": -24611937, "name": "ОБЪЯВЛЕНИЯ г МАЛМЫЖ", "category": "reklama"},
            {"vk_id": -170319760, "name": "Администрация Малмыжского района", "category": "admin"},
        ]

        imported_count = 0
        for comm_data in sample_communities:
            # Check if exists
            result = await session.execute(
                select(Community).where(Community.vk_id == comm_data["vk_id"])
            )
            existing = result.scalar_one_or_none()

            if existing:
                continue

            community = Community(
                region_id=mi_region.id,
                vk_id=comm_data["vk_id"],
                name=comm_data["name"],
                category=comm_data["category"],
                is_active=True,
            )

            session.add(community)
            imported_count += 1
            print(f"  ✅ Imported: {comm_data['name']}")

        await session.commit()
        print(f"\n✅ Imported {imported_count} communities")


async def import_vk_tokens():
    """Import VK tokens"""
    print("\n🔑 Importing VK tokens...")

    VK_TOKENS = _collect_prefixed_env("VK_TOKEN_")
    if not VK_TOKENS:
        print("  ⚠️  No VK_TOKEN_* env vars found, skipping token import")
        return

    async with AsyncSessionLocal() as session:
        imported_count = 0

        for name, token in VK_TOKENS.items():
            if not token:  # Skip empty tokens
                continue

            # Check if exists
            result = await session.execute(select(VKToken).where(VKToken.name == name))
            existing = result.scalar_one_or_none()

            if existing:
                print(f"  ⏭️  Token {name} already exists")
                continue

            vk_token = VKToken(name=name, token=token, is_active=True)

            session.add(vk_token)
            imported_count += 1
            print(f"  ✅ Imported: {name}")

        await session.commit()
        print(f"\n✅ Imported {imported_count} VK tokens")


async def import_filters():
    """Import filters/blacklists from config"""
    print("\n🔍 Importing filters...")

    # Common spam words
    blacklist_words = [
        "клиникинаедине",
        "банкомпойдём",
        "закажисейчас",
        "бонусовспортмастер",
        "микрозаймпод",
        "магазинзолушка",
        "потерялсякот",
        "закупайрекламу",
        "work",
        "заказатьможнотут",
        "призаказевподарок",
    ]

    async with AsyncSessionLocal() as session:
        imported_count = 0

        for word in blacklist_words:
            # Check if exists
            result = await session.execute(
                select(Filter).where(Filter.type == "blacklist_word", Filter.pattern == word)
            )
            existing = result.scalar_one_or_none()

            if existing:
                continue

            filter_obj = Filter(
                type="blacklist_word",
                pattern=word,
                action="delete",
                score_modifier=-100,
                description="Spam word from old project",
                is_active=True,
            )

            session.add(filter_obj)
            imported_count += 1

        await session.commit()
        print(f"✅ Imported {imported_count} filters")


async def main():
    print("=" * 60)
    print("🚀 Importing data from old Postopus project to SETKA")
    print("=" * 60)

    try:
        await import_regions()
        await import_vk_tokens()
        await import_filters()
        await import_communities()  # This is basic, will need manual work

        print("\n" + "=" * 60)
        print("✅ Data import completed!")
        print("=" * 60)
        print("\n⚠️  NOTE: Communities import is basic.")
        print("You'll need to manually add more communities from old DB.")

    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback

        traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
