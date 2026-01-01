#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Улучшенный скрипт импорта данных из Postopus в SETKA PostgreSQL
"""
import asyncio
import sys
import os
import json

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, func
from database.connection import AsyncSessionLocal
from database.models import Region, Community, Filter, VKToken

DATA_DIR = "/home/valstan/SETKA/old_project_analysis"


async def import_regions():
    """Импорт регионов из extracted data"""
    print("📍 Импорт регионов...")
    
    # Load extracted data
    with open(f'{DATA_DIR}/postopus_regions.json', 'r') as f:
        regions_data = json.load(f)
    
    async with AsyncSessionLocal() as session:
        imported_count = 0
        skipped_count = 0
        
        for region_code, region_info in regions_data.items():
            # Check if region already exists
            result = await session.execute(
                select(Region).where(Region.code == region_code)
            )
            existing = result.scalar_one_or_none()
            
            if existing:
                print(f"  ⏭️  {region_code} уже существует")
                skipped_count += 1
                continue
            
            # Create region
            region = Region(
                code=region_code,
                name=region_info['name_group'],
                vk_group_id=region_info.get('post_group_vk'),
                telegram_channel=region_info.get('post_group_telega', ''),
                neighbors=region_info.get('neighbors', ''),
                is_active=True
            )
            
            session.add(region)
            imported_count += 1
            print(f"  ✅ Импортирован: {region_code} - {region.name}")
        
        await session.commit()
        print(f"\n✅ Импортировано регионов: {imported_count}")
        print(f"⏭️  Пропущено (уже существуют): {skipped_count}")


async def import_communities():
    """Импорт сообществ VK"""
    print("\n📊 Импорт сообществ VK...")
    
    # Load extracted data
    with open(f'{DATA_DIR}/postopus_regions.json', 'r') as f:
        regions_data = json.load(f)
    
    async with AsyncSessionLocal() as session:
        imported_count = 0
        skipped_count = 0
        error_count = 0
        
        # Get all regions from DB to map codes to IDs
        regions_result = await session.execute(select(Region))
        regions = {r.code: r for r in regions_result.scalars().all()}
        
        for region_code, region_info in regions_data.items():
            if region_code not in regions:
                print(f"  ⚠️  Регион {region_code} не найден в БД, пропуск")
                continue
            
            region = regions[region_code]
            communities_list = region_info.get('communities', [])
            
            print(f"\n  📍 {region_code}: {len(communities_list)} сообществ")
            
            for comm_data in communities_list:
                try:
                    vk_id = comm_data['vk_id']
                    
                    # Check if community already exists
                    result = await session.execute(
                        select(Community).where(Community.vk_id == vk_id)
                    )
                    existing = result.scalar_one_or_none()
                    
                    if existing:
                        skipped_count += 1
                        continue
                    
                    # Create community
                    community = Community(
                        region_id=region.id,
                        vk_id=vk_id,
                        name=comm_data['name'],
                        category=comm_data['category'],
                        is_active=True
                    )
                    
                    session.add(community)
                    imported_count += 1
                    
                    # Commit every 100 communities for progress
                    if imported_count % 100 == 0:
                        await session.commit()
                        print(f"    ... {imported_count} импортировано")
                
                except Exception as e:
                    error_count += 1
                    print(f"    ⚠️  Ошибка импорта сообщества {comm_data.get('name', 'Unknown')}: {e}")
                    continue
        
        # Final commit
        await session.commit()
        
        print(f"\n✅ Импортировано сообществ: {imported_count}")
        print(f"⏭️  Пропущено (уже существуют): {skipped_count}")
        if error_count > 0:
            print(f"⚠️  Ошибок: {error_count}")


async def import_filters():
    """Импорт фильтров"""
    print("\n🔍 Импорт фильтров...")
    
    # Load extracted filters
    with open(f'{DATA_DIR}/postopus_filters.json', 'r') as f:
        filters_data = json.load(f)
    
    async with AsyncSessionLocal() as session:
        imported_count = 0
        skipped_count = 0
        
        # Import delete blacklist words
        for word in filters_data['blacklist_delete']:
            if not word or len(word) < 2:  # Skip empty or too short
                continue
            
            # Check if exists
            result = await session.execute(
                select(Filter).where(
                    Filter.type == 'blacklist_word',
                    Filter.pattern == word
                )
            )
            existing = result.scalar_one_or_none()
            
            if existing:
                skipped_count += 1
                continue
            
            filter_obj = Filter(
                type='blacklist_word',
                pattern=word,
                action='delete',
                score_modifier=-100,
                description='Spam word from Postopus (delete_msg_blacklist)',
                is_active=True
            )
            
            session.add(filter_obj)
            imported_count += 1
            
            # Commit every 100 filters
            if imported_count % 100 == 0:
                await session.commit()
        
        # Import clear text blacklist words
        for word in filters_data['blacklist_clear']:
            if not word or len(word) < 2:
                continue
            
            result = await session.execute(
                select(Filter).where(
                    Filter.type == 'clear_text',
                    Filter.pattern == word
                )
            )
            existing = result.scalar_one_or_none()
            
            if existing:
                skipped_count += 1
                continue
            
            filter_obj = Filter(
                type='clear_text',
                pattern=word,
                action='clean',
                score_modifier=0,
                description='Text cleaning pattern from Postopus',
                is_active=True
            )
            
            session.add(filter_obj)
            imported_count += 1
        
        # Import black IDs
        for black_id in filters_data['black_ids']:
            if not black_id:
                continue
            
            result = await session.execute(
                select(Filter).where(
                    Filter.type == 'black_id',
                    Filter.pattern == str(black_id)
                )
            )
            existing = result.scalar_one_or_none()
            
            if existing:
                skipped_count += 1
                continue
            
            filter_obj = Filter(
                type='black_id',
                pattern=str(black_id),
                action='delete',
                score_modifier=-1000,
                description='Blacklisted user/group ID from Postopus',
                is_active=True
            )
            
            session.add(filter_obj)
            imported_count += 1
        
        # Import bad name groups
        for bad_name in filters_data['bad_name_groups']:
            if not bad_name:
                continue
            
            result = await session.execute(
                select(Filter).where(
                    Filter.type == 'bad_name',
                    Filter.pattern == bad_name
                )
            )
            existing = result.scalar_one_or_none()
            
            if existing:
                skipped_count += 1
                continue
            
            filter_obj = Filter(
                type='bad_name',
                pattern=bad_name,
                action='skip_attribution',
                score_modifier=0,
                description='Bad group name from Postopus (hide attribution)',
                is_active=True
            )
            
            session.add(filter_obj)
            imported_count += 1
        
        # Final commit
        await session.commit()
        
        print(f"✅ Импортировано фильтров: {imported_count}")
        print(f"⏭️  Пропущено (уже существуют): {skipped_count}")


async def import_vk_tokens():
    """Импорт VK токенов из конфига"""
    print("\n🔑 Импорт VK токенов...")
    
    try:
        from config.runtime import VK_TOKENS
    except ImportError:
        print("  ⚠️  config_secure.VK_TOKENS не найден, пропуск")
        return
    
    async with AsyncSessionLocal() as session:
        imported_count = 0
        skipped_count = 0
        
        for name, token in VK_TOKENS.items():
            if not token or len(token) < 10:  # Skip empty or invalid tokens
                continue
            
            # Check if exists
            result = await session.execute(
                select(VKToken).where(VKToken.name == name)
            )
            existing = result.scalar_one_or_none()
            
            if existing:
                print(f"  ⏭️  Токен {name} уже существует")
                skipped_count += 1
                continue
            
            # Determine usage type based on name
            if 'VALSTAN' in name.upper():
                usage_type = 'post'
            elif 'DRAN' in name.upper():
                usage_type = 'read'
            elif 'OLGA' in name.upper() or 'VITA' in name.upper():
                usage_type = 'read'
            else:
                usage_type = 'read'
            
            vk_token = VKToken(
                name=name,
                token=token,
                usage_type=usage_type,
                is_active=True
            )
            
            session.add(vk_token)
            imported_count += 1
            print(f"  ✅ Импортирован: {name} ({usage_type})")
        
        await session.commit()
        print(f"\n✅ Импортировано токенов: {imported_count}")
        if skipped_count > 0:
            print(f"⏭️  Пропущено (уже существуют): {skipped_count}")


async def show_statistics():
    """Показать итоговую статистику БД"""
    print("\n" + "=" * 70)
    print("📊 Итоговая статистика БД SETKA")
    print("=" * 70)
    
    async with AsyncSessionLocal() as session:
        # Count regions
        regions_result = await session.execute(select(func.count(Region.id)))
        regions_count = regions_result.scalar()
        
        # Count communities
        communities_result = await session.execute(select(func.count(Community.id)))
        communities_count = communities_result.scalar()
        
        # Count filters
        filters_result = await session.execute(select(func.count(Filter.id)))
        filters_count = filters_result.scalar()
        
        # Count VK tokens
        tokens_result = await session.execute(select(func.count(VKToken.id)))
        tokens_count = tokens_result.scalar()
        
        # Count active communities
        active_comm_result = await session.execute(
            select(func.count(Community.id)).where(Community.is_active == True)
        )
        active_communities_count = active_comm_result.scalar()
        
        print(f"\n  📍 Регионов: {regions_count}")
        print(f"  📊 Сообществ VK: {communities_count} ({active_communities_count} активных)")
        print(f"  🔍 Фильтров: {filters_count}")
        print(f"  🔑 VK токенов: {tokens_count}")


async def main():
    print("=" * 70)
    print("🚀 Импорт данных из Postopus в SETKA PostgreSQL")
    print("=" * 70)
    
    try:
        await import_regions()
        await import_vk_tokens()
        await import_filters()
        await import_communities()
        await show_statistics()
        
        print("\n" + "=" * 70)
        print("✅ Импорт данных завершен успешно!")
        print("=" * 70)
        
        return 0
        
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)

