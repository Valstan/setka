#!/usr/bin/env python3
"""
Test full workflow: Monitor → Analyze → Publish
"""
import asyncio
import sys
import logging
from pathlib import Path
import os

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))
os.chdir(project_root)

from modules.scheduler.scheduler import ContentScheduler
from modules.publisher.publisher import ContentPublisher
from config.config_secure import VK_TOKENS, TELEGRAM_TOKENS, GROQ_API_KEY

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)


async def main():
    """Test full workflow"""
    print("\n" + "=" * 60)
    print("🧪 TESTING FULL WORKFLOW")
    print("=" * 60 + "\n")
    
    try:
        # Initialize publisher
        print("📤 Initializing publishers...")
        vk_token = next((token for token in VK_TOKENS.values() if token), None)
        telegram_token = TELEGRAM_TOKENS.get("AFONYA")
        
        publisher = ContentPublisher(
            vk_token=vk_token,
            telegram_token=telegram_token
        )
        
        # Check connections
        print("\n🔍 Checking publisher connections...")
        connections = await publisher.check_all_connections()
        for platform, status in connections.items():
            print(f"   {platform.upper()}: {'✅ OK' if status else '❌ FAILED'}")
        
        # Initialize scheduler
        print("\n⚙️ Initializing scheduler...")
        tokens = [token for token in VK_TOKENS.values() if token]
        
        scheduler = ContentScheduler(
            vk_tokens=tokens,
            groq_api_key=GROQ_API_KEY,
            publisher=publisher
        )
        
        # Get current pipeline stats
        print("\n📊 Current pipeline stats:")
        stats = await scheduler.get_pipeline_stats()
        print(f"   Total posts: {stats['total_posts']}")
        print(f"   By status:")
        for status, count in stats['by_status'].items():
            print(f"      {status}: {count}")
        
        print(f"\n   Pipeline:")
        for stage, count in stats['pipeline'].items():
            print(f"      {stage}: {count}")
        
        # Run full cycle
        print("\n" + "=" * 60)
        print("🔄 Running full cycle...")
        print("=" * 60)
        
        result = await scheduler.run_full_cycle()
        
        if result.get('status') == 'success':
            print("\n✅ Full cycle completed successfully!\n")
            print(f"Duration: {result['duration_seconds']:.1f}s")
            print(f"\nMonitoring:")
            print(f"   New posts found: {result['monitoring']['new_posts']}")
            print(f"\nAnalysis:")
            print(f"   Analyzed: {result['analysis']['analyzed']}")
            print(f"   Approved: {result['analysis']['approved']}")
            print(f"   Rejected: {result['analysis']['rejected']}")
            print(f"\nPublishing:")
            print(f"   Published: {result['publishing']['published']}")
        else:
            print(f"\n❌ Full cycle failed: {result.get('error')}")
        
        # Get updated stats
        print("\n📊 Updated pipeline stats:")
        stats = await scheduler.get_pipeline_stats()
        print(f"   Ready to publish: {stats['pipeline']['ready_to_publish']}")
        print(f"   Published: {stats['pipeline']['published']}")
        
        print("\n" + "=" * 60)
        print("✅ Test completed!")
        print("=" * 60 + "\n")
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)

