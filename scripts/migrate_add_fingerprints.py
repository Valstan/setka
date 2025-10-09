#!/usr/bin/env python3
"""
Database migration: Add fingerprint fields to posts table
Based on Postopus proven deduplication patterns
"""
import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text
from database.connection import AsyncSessionLocal, engine


async def run_migration():
    """Add fingerprint columns to posts table"""
    
    print("🔄 Starting migration: Add fingerprint fields...")
    print()
    
    async with engine.begin() as conn:
        # Check if columns already exist
        check_query = """
        SELECT column_name 
        FROM information_schema.columns 
        WHERE table_name = 'posts' 
        AND column_name IN ('fingerprint_lip', 'fingerprint_media', 'fingerprint_text', 'fingerprint_text_core')
        """
        
        result = await conn.execute(text(check_query))
        existing_columns = [row[0] for row in result]
        
        if len(existing_columns) == 4:
            print("✅ All fingerprint columns already exist. Migration not needed.")
            return
        
        if existing_columns:
            print(f"⚠️ Some columns already exist: {existing_columns}")
            print("   Skipping existing columns...")
        
        # Add fingerprint columns
        migrations = []
        
        if 'fingerprint_lip' not in existing_columns:
            migrations.append("""
                ALTER TABLE posts 
                ADD COLUMN fingerprint_lip VARCHAR(50)
            """)
            migrations.append("""
                CREATE INDEX ix_posts_fingerprint_lip 
                ON posts(fingerprint_lip)
            """)
        
        if 'fingerprint_media' not in existing_columns:
            migrations.append("""
                ALTER TABLE posts 
                ADD COLUMN fingerprint_media JSON
            """)
        
        if 'fingerprint_text' not in existing_columns:
            migrations.append("""
                ALTER TABLE posts 
                ADD COLUMN fingerprint_text VARCHAR(100)
            """)
            migrations.append("""
                CREATE INDEX ix_posts_fingerprint_text 
                ON posts(fingerprint_text)
            """)
        
        if 'fingerprint_text_core' not in existing_columns:
            migrations.append("""
                ALTER TABLE posts 
                ADD COLUMN fingerprint_text_core VARCHAR(100)
            """)
            migrations.append("""
                CREATE INDEX ix_posts_fingerprint_text_core 
                ON posts(fingerprint_text_core)
            """)
        
        # Execute migrations
        for i, query in enumerate(migrations, 1):
            print(f"Executing migration {i}/{len(migrations)}...")
            await conn.execute(text(query))
        
        print()
        print(f"✅ Migration completed! Added {len(migrations)} changes.")


async def backfill_fingerprints():
    """Backfill fingerprints for existing posts"""
    
    print()
    print("🔄 Backfilling fingerprints for existing posts...")
    print()
    
    from database.models import Post
    from sqlalchemy import select
    from modules.deduplication import (
        create_lip_fingerprint,
        create_media_fingerprint,
        create_text_fingerprint,
        create_text_core_fingerprint
    )
    
    async with AsyncSessionLocal() as session:
        # Get all posts without fingerprints
        result = await session.execute(
            select(Post).where(Post.fingerprint_lip.is_(None))
        )
        posts = result.scalars().all()
        
        if not posts:
            print("✅ No posts need backfilling.")
            return
        
        print(f"Found {len(posts)} posts to backfill...")
        
        updated = 0
        for post in posts:
            try:
                # Create fingerprints
                post.fingerprint_lip = create_lip_fingerprint(post.vk_owner_id, post.vk_post_id)
                
                if post.attachments:
                    post.fingerprint_media = create_media_fingerprint(post.attachments)
                
                if post.text:
                    post.fingerprint_text = create_text_fingerprint(post.text)
                    post.fingerprint_text_core = create_text_core_fingerprint(post.text)
                
                updated += 1
                
                if updated % 10 == 0:
                    print(f"  Processed {updated}/{len(posts)} posts...")
                    await session.commit()
            
            except Exception as e:
                print(f"  ⚠️ Error processing post {post.id}: {e}")
                continue
        
        await session.commit()
        print()
        print(f"✅ Backfill completed! Updated {updated} posts.")


async def verify_migration():
    """Verify migration was successful"""
    
    print()
    print("🔍 Verifying migration...")
    print()
    
    async with engine.begin() as conn:
        # Count posts with fingerprints
        query = """
        SELECT 
            COUNT(*) as total,
            COUNT(fingerprint_lip) as with_lip,
            COUNT(fingerprint_text) as with_text,
            COUNT(fingerprint_text_core) as with_core
        FROM posts
        """
        
        result = await conn.execute(text(query))
        row = result.fetchone()
        
        print(f"Total posts: {row[0]}")
        print(f"  With lip fingerprint: {row[1]}")
        print(f"  With text fingerprint: {row[2]}")
        print(f"  With core fingerprint: {row[3]}")
        print()
        
        if row[0] > 0 and row[1] == row[0]:
            print("✅ All posts have fingerprints!")
        elif row[0] == 0:
            print("ℹ️ No posts in database yet.")
        else:
            print("⚠️ Some posts are missing fingerprints.")


async def main():
    """Run migration"""
    print("=" * 60)
    print("DATABASE MIGRATION: Add Fingerprints")
    print("Based on Postopus proven patterns (3+ years in production)")
    print("=" * 60)
    print()
    
    try:
        # Step 1: Add columns
        await run_migration()
        
        # Step 2: Backfill existing data
        await backfill_fingerprints()
        
        # Step 3: Verify
        await verify_migration()
        
        print()
        print("=" * 60)
        print("✅ MIGRATION SUCCESSFUL!")
        print("=" * 60)
        
        return 0
    
    except Exception as e:
        print()
        print("=" * 60)
        print("❌ MIGRATION FAILED!")
        print("=" * 60)
        print()
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)

