"""
Migration script: MongoDB config → PostgreSQL (SETKA)

Reads configuration from old_postopus MongoDB and migrates it to SETKA PostgreSQL.
This includes:
- Region configs (zagolovki, heshteg, black_id, etc.)
- Communities (all_my_groups → communities table)
- Filters (region words, blacklists)
- Work tables (lip, hash, bezfoto)
"""
import asyncio
import os
import sys
from datetime import datetime
from typing import Dict, List, Any, Optional

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.connection import init_db, async_session_maker
from database.models import Region, Community, Filter
from database.models_extended import RegionConfig, WorkTable


# MongoDB connection - read from env or default
MONGO_URI = os.getenv("MONGO_CLIENT", "mongodb://localhost:27017")
MONGO_DB = "postopus"

# Region name → collection code mapping (from old_postopus get_session.py)
REGION_MAPPING = {
    "ВП - Инфо": "vp",
    "Малмыж - Инфо": "mi",
    "Уржум - Инфо": "ur",
    "Советск - Инфо": "sovetsk",
    "Нолинск - Инфо": "nolinsk",
    "Арбаж - Инфо": "arbazh",
    "Нема - Инфо": "nema",
    "Кильмезь - Инфо": "klz",
    "Пижанка - Инфо": "pizhanka",
    "Верхошижемье - Инфо": "verhoshizhem",
    "Лебяжье - Инфо": "leb",
    "Балтаси - Инфо": "bal",
    "Кукмор - Инфо": "kukmor",
    "Гоньба - жемчужина Вятки": "gonba",
    "Кировская область - Инфо": "kirov_obl",
}

# Theme mapping
THEMES = ["novost", "kultura", "sport", "detsad", "admin", "union", "reklama", "sosed"]

# Category mapping for communities
CATEGORY_MAPPING = {
    "novost": "novost",
    "kultura": "kultura",
    "sport": "sport",
    "detsad": "detsad",
    "admin": "admin",
    "union": "union",
    "reklama": "reklama",
    "sosed": "sosed",
}


def connect_mongo():
    """Connect to old_postopus MongoDB"""
    try:
        from pymongo import MongoClient
        client = MongoClient(MONGO_URI)
        db = client[MONGO_DB]
        print(f"✅ Connected to MongoDB: {MONGO_URI}/{MONGO_DB}")
        return db
    except ImportError:
        print("❌ pymongo not installed. Install with: pip install pymongo")
        sys.exit(1)
    except Exception as e:
        print(f"❌ MongoDB connection failed: {e}")
        sys.exit(1)


async def migrate_region_config(mongo_db, session) -> int:
    """Migrate MongoDB config collection → Region + RegionConfig tables"""
    print("\n📦 Migrating region configuration...")
    
    config_doc = mongo_db.config.find_one({"title": "config"})
    if not config_doc:
        print("⚠️  No config document found in MongoDB")
        return 0
    
    migrated = 0
    
    for group_name, group_id in config_doc.get("all_my_groups", {}).items():
        if group_name not in REGION_MAPPING:
            print(f"  ⚠️  Unknown region: {group_name}")
            continue
        
        region_code = REGION_MAPPING[group_name]
        
        # Check if region exists
        result = await session.execute(
            Region.__table__.select().where(Region.code == region_code)
        )
        region = result.scalar_one_or_none()
        
        if not region:
            # Create region
            region = Region(
                code=region_code,
                name=group_name.upper(),
                vk_group_id=abs(group_id),
                is_active=True,
            )
            session.add(region)
            await session.flush()
            print(f"  ✅ Created region: {region_code} ({group_name})")
        
        # Create/update extended config
        result = await session.execute(
            RegionConfig.__table__.select().where(RegionConfig.region_code == region_code)
        )
        existing_config = result.scalar_one_or_none()
        
        if existing_config:
            # Update existing
            existing_config.zagolovki = config_doc.get("zagolovki", {})
            existing_config.heshteg = config_doc.get("heshteg", {})
            existing_config.heshteg_local = config_doc.get("heshteg_local", {})
            existing_config.black_id = config_doc.get("black_id", [])
            existing_config.filter_group_by_region_words = config_doc.get("filter_group_by_region_words", {})
            existing_config.region_words = config_doc.get("kirov_words", {})
            existing_config.region_words = {**existing_config.region_words, **config_doc.get("tatar_words", {})}
            existing_config.only_main_news = config_doc.get("only_main_news", [])
            existing_config.time_old_post = config_doc.get("time_old_post", {"hard": 86400, "medium": 172800, "light": 604800})
            existing_config.text_post_maxsize_simbols = config_doc.get("text_post_maxsize_simbols", 4096)
            existing_config.delete_msg_blacklist = config_doc.get("delete_msg_blacklist", [])
            existing_config.fast_del_msg_blacklist = config_doc.get("fast_del_msg_blacklist", [])
            existing_config.clear_text_blacklist = config_doc.get("clear_text_blacklist", [])
            existing_config.sosed = config_doc.get("sosed", "")
            existing_config.setka_regim_repost = config_doc.get("setka_regim_repost", False)
            existing_config.baraban = config_doc.get("baraban", [])
            existing_config.repost_words_blacklist = config_doc.get("repost_words_black_list", [])
            existing_config.mongo_collection_name = region_code
            print(f"  🔄 Updated config: {region_code}")
        else:
            # Create new
            region_config = RegionConfig(
                region_code=region_code,
                zagolovki=config_doc.get("zagolovki", {}),
                heshteg=config_doc.get("heshteg", {}),
                heshteg_local=config_doc.get("heshteg_local", {}),
                black_id=config_doc.get("black_id", []),
                filter_group_by_region_words=config_doc.get("filter_group_by_region_words", {}),
                region_words={**config_doc.get("kirov_words", {}), **config_doc.get("tatar_words", {})},
                only_main_news=config_doc.get("only_main_news", []),
                time_old_post=config_doc.get("time_old_post", {"hard": 86400, "medium": 172800, "light": 604800}),
                text_post_maxsize_simbols=config_doc.get("text_post_maxsize_simbols", 4096),
                delete_msg_blacklist=config_doc.get("delete_msg_blacklist", []),
                fast_del_msg_blacklist=config_doc.get("fast_del_msg_blacklist", []),
                clear_text_blacklist=config_doc.get("clear_text_blacklist", []),
                sosed=config_doc.get("sosed", ""),
                setka_regim_repost=config_doc.get("setka_regim_repost", False),
                baraban=config_doc.get("baraban", []),
                repost_words_blacklist=config_doc.get("repost_words_black_list", []),
                mongo_collection_name=region_code,
            )
            session.add(region_config)
            print(f"  ✅ Created config: {region_code}")
        
        migrated += 1
    
    return migrated


async def migrate_communities(mongo_db, session) -> int:
    """Migrate MongoDB communities → Community table"""
    print("\n👥 Migrating communities...")
    
    config_doc = mongo_db.config.find_one({"title": "config"})
    if not config_doc:
        print("⚠️  No config document found")
        return 0
    
    migrated = 0
    
    for group_name, group_id in config_doc.get("all_my_groups", {}).items():
        if group_name not in REGION_MAPPING:
            continue
        
        region_code = REGION_MAPPING[group_name]
        
        # Get region ID
        result = await session.execute(
            Region.__table__.select().where(Region.code == region_code)
        )
        region = result.scalar_one_or_none()
        if not region:
            continue
        
        # Get theme groups from config
        for theme in THEMES:
            theme_groups = config_doc.get(theme, {})
            for theme_group_name, theme_group_id in theme_groups.items():
                # Check if already exists
                result = await session.execute(
                    Community.__table__.select().where(
                        Community.vk_id == abs(theme_group_id),
                        Community.category == theme
                    )
                )
                existing = result.scalar_one_or_none()
                
                if not existing:
                    community = Community(
                        region_id=region.id,
                        vk_id=abs(theme_group_id),
                        screen_name=None,
                        name=theme_group_name,
                        category=theme,
                        is_active=True,
                        check_interval=300,
                    )
                    session.add(community)
                    migrated += 1
                    print(f"  ✅ Added community: {theme_group_name} ({theme})")
    
    return migrated


async def migrate_filters(mongo_db, session) -> int:
    """Migrate MongoDB filters → Filter table"""
    print("\n🔧 Migrating filters...")
    
    config_doc = mongo_db.config.find_one({"title": "config"})
    if not config_doc:
        return 0
    
    migrated = 0
    
    # Migrate delete_msg_blacklist → blacklist_word filters
    blacklist = config_doc.get("delete_msg_blacklist", [])
    for pattern in blacklist:
        result = await session.execute(
            Filter.__table__.select().where(
                Filter.type == "blacklist_word",
                Filter.pattern == pattern
            )
        )
        existing = result.scalar_one_or_none()
        
        if not existing:
            f = Filter(
                type="blacklist_word",
                pattern=pattern,
                action="delete",
                score_modifier=-100,
                description=f"Blacklisted word/phrase from MongoDB migration",
                is_active=True,
            )
            session.add(f)
            migrated += 1
    
    # Migrate region words → region_word filters
    kirov_words = config_doc.get("kirov_words", {})
    for region_word, words in kirov_words.items():
        for word in words:
            result = await session.execute(
                Filter.__table__.select().where(
                    Filter.type == "region_word",
                    Filter.pattern == word
                )
            )
            existing = result.scalar_one_or_none()
            
            if not existing:
                f = Filter(
                    type="region_word",
                    category="kirov",
                    pattern=word,
                    action="flag",
                    score_modifier=10,
                    description=f"Region word for {region_word}",
                    is_active=True,
                )
                session.add(f)
                migrated += 1
    
    tatar_words = config_doc.get("tatar_words", {})
    for region_word, words in tatar_words.items():
        for word in words:
            result = await session.execute(
                Filter.__table__.select().where(
                    Filter.type == "region_word",
                    Filter.pattern == word
                )
            )
            existing = result.scalar_one_or_none()
            
            if not existing:
                f = Filter(
                    type="region_word",
                    category="tatar",
                    pattern=word,
                    action="flag",
                    score_modifier=10,
                    description=f"Region word for {region_word}",
                    is_active=True,
                )
                session.add(f)
                migrated += 1
    
    print(f"  ✅ Migrated {migrated} filters")
    return migrated


async def migrate_work_tables(mongo_db, session) -> int:
    """Migrate MongoDB work tables (lip, hash, bezfoto) → WorkTable"""
    print("\n📊 Migrating work tables...")
    
    config_doc = mongo_db.config.find_one({"title": "config"})
    if not config_doc:
        return 0
    
    migrated = 0
    
    # Reverse mapping: collection code → region code
    reverse_mapping = {v: k for k, v in REGION_MAPPING.items()}
    
    for collection_code, group_name in REGION_MAPPING.items():
        # Get MongoDB collection for this region
        collection = mongo_db[collection_code]
        
        for theme in THEMES:
            # Get work table document
            doc = collection.find_one({"title": theme})
            if not doc:
                continue
            
            region_code = collection_code
            
            # Check if exists
            result = await session.execute(
                WorkTable.__table__.select().where(
                    WorkTable.region_code == region_code,
                    WorkTable.theme == theme
                )
            )
            existing = result.scalar_one_or_none()
            
            if existing:
                existing.lip = doc.get("lip", [])
                existing.hash = doc.get("hash", [])
                existing.bezfoto = doc.get("bezfoto", [])
                existing.all_bezfoto = doc.get("all_bezfoto", [])
                print(f"  🔄 Updated work table: {region_code}/{theme}")
            else:
                wt = WorkTable(
                    region_code=region_code,
                    theme=theme,
                    lip=doc.get("lip", []),
                    hash=doc.get("hash", []),
                    bezfoto=doc.get("bezfoto", []),
                    all_bezfoto=doc.get("all_bezfoto", []),
                )
                session.add(wt)
                print(f"  ✅ Created work table: {region_code}/{theme}")
            
            migrated += 1
    
    print(f"  📊 Total work tables: {migrated}")
    return migrated


async def main():
    """Main migration function"""
    print("=" * 60)
    print("🔄 MongoDB → PostgreSQL Migration Script")
    print("=" * 60)
    print(f"MongoDB: {MONGO_URI}/{MONGO_DB}")
    print(f"PostgreSQL: {os.getenv('DATABASE_URL', 'NOT SET')}")
    print("=" * 60)
    
    # Check MongoDB connection
    mongo_db = connect_mongo()
    
    # Initialize PostgreSQL
    await init_db()
    
    stats = {
        "regions": 0,
        "communities": 0,
        "filters": 0,
        "work_tables": 0,
    }
    
    async with async_session_maker() as session:
        try:
            # Step 1: Migrate region configs
            stats["regions"] = await migrate_region_config(mongo_db, session)
            
            # Step 2: Migrate communities
            stats["communities"] = await migrate_communities(mongo_db, session)
            
            # Step 3: Migrate filters
            stats["filters"] = await migrate_filters(mongo_db, session)
            
            # Step 4: Migrate work tables
            stats["work_tables"] = await migrate_work_tables(mongo_db, session)
            
            # Commit all changes
            await session.commit()
            
            print("\n" + "=" * 60)
            print("✅ Migration completed successfully!")
            print("=" * 60)
            print(f"  Regions: {stats['regions']}")
            print(f"  Communities: {stats['communities']}")
            print(f"  Filters: {stats['filters']}")
            print(f"  Work tables: {stats['work_tables']}")
            print("=" * 60)
            
        except Exception as e:
            await session.rollback()
            print(f"\n❌ Migration failed: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
