#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test script for VK Monitor
"""
import asyncio
import sys
import os
import logging

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

from modules.vk_monitor.monitor import VKMonitor
from database.connection import AsyncSessionLocal
from database.models import VKToken
from sqlalchemy import select


async def get_vk_tokens_from_db():
    """Get VK tokens from database"""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(VKToken).where(VKToken.is_active == True)
        )
        tokens_objs = result.scalars().all()
        return [t.token for t in tokens_objs if t.token]


async def test_scan_single_region():
    """Test scanning a single region"""
    print("=" * 60)
    print("🧪 Testing VK Monitor - Single Region Scan")
    print("=" * 60)
    
    # Get tokens from database
    tokens = await get_vk_tokens_from_db()
    
    if not tokens:
        print("❌ No VK tokens available. Run scripts/add_vk_tokens.py first")
        return
    
    print(f"✅ Found {len(tokens)} VK tokens")
    
    # Create monitor
    monitor = VKMonitor(vk_tokens=tokens)
    
    # Test scanning Малмыж region
    print("\n📍 Scanning region: mi (Малмыж)")
    result = await monitor.scan_region('mi')
    
    print("\n📊 Results:")
    print(f"  Communities scanned: {result.get('communities', 0)}")
    print(f"  New posts found: {result.get('new_posts', 0)}")
    
    if 'error' in result:
        print(f"  ❌ Error: {result['error']}")


async def test_scan_all_regions():
    """Test scanning all regions"""
    print("\n" + "=" * 60)
    print("🧪 Testing VK Monitor - All Regions Scan")
    print("=" * 60)
    
    # Get tokens from database
    tokens = await get_vk_tokens_from_db()
    
    if not tokens:
        print("❌ No VK tokens available")
        return
    
    monitor = VKMonitor(vk_tokens=tokens)
    
    print("\n🌍 Scanning all active regions...")
    result = await monitor.scan_all_regions()
    
    print("\n📊 Overall Results:")
    print(f"  Timestamp: {result['timestamp']}")
    print(f"  Regions scanned: {result['regions_scanned']}")
    print(f"  Total communities: {result['total_communities']}")
    print(f"  Total new posts: {result['total_new_posts']}")
    
    print("\n📋 Details by region:")
    for region_code, region_result in result.get('details', {}).items():
        print(f"  {region_code}: {region_result.get('new_posts', 0)} posts from {region_result.get('communities', 0)} communities")


async def main():
    """Main test function"""
    print("🚀 VK Monitor Test Suite\n")
    
    try:
        # Test 1: Single region scan
        await test_scan_single_region()
        
        # Wait a bit
        await asyncio.sleep(2)
        
        # Test 2: All regions scan
        # await test_scan_all_regions()  # Uncomment to test all regions
        
        print("\n" + "=" * 60)
        print("✅ Tests completed!")
        print("=" * 60)
        
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)

