#!/usr/bin/env python3
"""
Test production parse_and_publish_theme task manually.
Runs: parse_and_publish_theme(region_code='test', theme='novost', test_mode=False)
"""
import os
import sys

sys.path.insert(0, "/home/valstan/SETKA")

# Load env
os.environ.setdefault("DATABASE_URL", "")  # Will be loaded from env file

import asyncio
from datetime import datetime


async def main():
    print("=" * 70)
    print("PRODUCTION PIPELINE TEST: region='test', theme='novost'")
    print(f"Time: {datetime.now()}")
    print("=" * 70)

    from sqlalchemy import select

    from config.runtime import get_parse_tokens
    from database.connection import AsyncSessionLocal
    from database.models import Community, Region
    from database.models_extended import RegionConfig, WorkTable
    from modules.publisher.digest_builder import DigestBuilder
    from modules.publisher.digest_splitter import DigestSplitter
    from modules.publisher.postopus_digest_headers import (
        resolve_digest_hashtags,
        resolve_digest_header,
        resolve_mourning_digest_format,
    )
    from modules.publisher.vk_publisher_extended import VKPublisher
    from modules.vk_monitor.advanced_parser import AdvancedVKParser
    from modules.vk_monitor.vk_client import VKClient

    region_code = "test"
    theme = "novost"
    test_mode = False  # Real publish

    async with AsyncSessionLocal() as session:
        # 1. Region config
        result = await session.execute(
            select(RegionConfig).where(RegionConfig.region_code == region_code)
        )
        region_config = result.scalars().first()
        if not region_config:
            print(f"❌ RegionConfig not found for '{region_code}'")
            return
        print(f"✅ RegionConfig: zagolovki={region_config.zagolovki}")
        print(f"   heshteg={region_config.heshteg}")
        print(f"   heshteg_local={region_config.heshteg_local}")
        print(f"   text_post_maxsize_simbols={region_config.text_post_maxsize_simbols}")
        print(f"   setka_regim_repost={region_config.setka_regim_repost}")

        # 2. Work table
        result = await session.execute(
            select(WorkTable).where(WorkTable.region_code == region_code, WorkTable.theme == theme)
        )
        work_table = result.scalars().first()
        if work_table:
            print(f"✅ WorkTable: {len(work_table.lip or [])} LIP entries")
        else:
            print(f"⚠️ No WorkTable for {region_code}/{theme}")
            work_table = WorkTable(region_code=region_code, theme=theme, lip=[], hash=[])
            session.add(work_table)
            await session.commit()

        # 3. Communities
        region_obj = await session.execute(select(Region).where(Region.code == region_code))
        region_obj = region_obj.scalars().first()
        if not region_obj:
            print(f"❌ Region not found: {region_code}")
            return
        print(f"✅ Region: {region_obj.name} (vk_group_id={region_obj.vk_group_id})")

        communities_result = await session.execute(
            select(Community.vk_id, Community.name, Community.category).where(
                Community.region_id == region_obj.id,
                Community.category == theme,
                Community.is_active.is_(True),
            )
        )
        communities = communities_result.fetchall()
        print(f"✅ Communities for '{theme}': {len(communities)}")
        for c in communities:
            print(f"   - {c[1]:40s} vk_id={c[0]}")

        community_vk_ids = [c[0] for c in communities]
        if not community_vk_ids:
            print(f"⚠️ No communities for theme '{theme}' — trying all active")
            all_result = await session.execute(
                select(Community.vk_id).where(
                    Community.region_id == region_obj.id, Community.is_active.is_(True)
                )
            )
            community_vk_ids = [row[0] for row in all_result.fetchall()]
            print(f"   Using {len(community_vk_ids)} all active communities instead")

        if not community_vk_ids:
            print("❌ No communities at all!")
            return

        # 4. Parse
        parse_tokens = get_parse_tokens()
        parse_token = next(iter(parse_tokens.values()))
        print(f"\n🔍 Parsing with {len(community_vk_ids)} communities...")
        vk_client = VKClient(parse_token)
        parser = AdvancedVKParser(vk_client)
        posts = await parser.parse_posts_from_communities(
            community_ids=community_vk_ids,
            theme=theme,
            region_config=region_config,
            work_table_lip=work_table.lip or [],
            work_table_hash=work_table.hash or [],
            count_per_community=20,
        )
        stats = parser.get_stats()
        print(
            f"✅ Parsed {len(posts)} posts (scanned: {stats['total_posts_scanned']}, filtered dupes: {stats['posts_filtered_duplicate_lip']})"  # noqa: E501
        )

        if not posts:
            print("⚠️ No posts after filtering")
            return

        # 5. Split by sentiment
        splitter = DigestSplitter()
        mourning_posts, regular_posts = splitter.split_posts(posts)
        print(f"📊 Split: {len(mourning_posts)} mourning, {len(regular_posts)} regular")

        # 6. Build and publish digests
        header = resolve_digest_header(region_config, theme, region_obj)
        theme_tags, local_hashtag = resolve_digest_hashtags(region_config, theme)

        comm_meta = await session.execute(
            select(Community.vk_id, Community.name).where(Community.region_id == region_obj.id)
        )
        group_names = {str(abs(row[0])): row[1] for row in comm_meta.fetchall()}

        results = []

        # Regular digest
        if regular_posts:
            builder = DigestBuilder(
                header=header,
                hashtags=theme_tags,
                local_hashtag=local_hashtag,
                max_text_length=region_config.text_post_maxsize_simbols or 4096,
                repost_mode=region_config.setka_regim_repost,
            )
            digest = builder.build_digest(regular_posts, group_names=group_names)
            print(f"\n📝 Regular digest: {digest.post_count} posts, {digest.total_length} chars")

            vk_publisher = VKPublisher(test_polygon_mode=test_mode)
            publish_result = await vk_publisher.publish_digest(
                group_id=region_obj.vk_group_id,
                text=digest.text,
                attachments=digest.attachments_list,
            )
            print(f"   Publish: {publish_result}")
            results.append(("regular", digest, publish_result))

        # Mourning digest
        if mourning_posts:
            mourning_header, mourning_tags, mourning_local_hashtag = (
                resolve_mourning_digest_format()
            )
            mourning_builder = DigestBuilder(
                header=mourning_header,
                hashtags=mourning_tags,
                local_hashtag=mourning_local_hashtag,
                max_text_length=region_config.text_post_maxsize_simbols or 4096,
            )
            mourning_digest = mourning_builder.build_digest(mourning_posts, group_names=group_names)
            print(
                f"\nMourning digest: {mourning_digest.post_count} posts, {mourning_digest.total_length} chars"  # noqa: E501
            )

            vk_pub_m = VKPublisher(test_polygon_mode=test_mode)
            mourning_pub = await vk_pub_m.publish_digest(
                group_id=region_obj.vk_group_id,
                text=mourning_digest.text,
                attachments=mourning_digest.attachments_list,
            )
            print(f"   Publish: {mourning_pub}")
            results.append(("mourning", mourning_digest, mourning_pub))

        # Update work table
        all_included = []
        for _, d, _ in results:
            all_included.extend(d.posts_included)
        if all_included and work_table:
            existing = work_table.lip or []
            existing.extend(all_included)
            if len(existing) > 30:
                existing = existing[-30:]
            work_table.lip = existing
            await session.commit()
            print(f"\n💾 WorkTable updated: {len(existing)} LIP entries")

        # Summary
        print("\n" + "=" * 70)
        for kind, d, pr in results:
            print(f"{kind.upper()}: {d.post_count} posts → {pr.get('url', 'FAILED')}")
        print("=" * 70)


try:
    asyncio.run(main())
except Exception as e:
    print(f"❌ ERROR: {e}", file=sys.stderr)
    import traceback

    traceback.print_exc()
