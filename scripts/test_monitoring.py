#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test monitoring system
"""
import asyncio
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.monitoring.health_checker import HealthChecker
from modules.monitoring.telegram_notifier import TelegramNotifier

# Telegram bot token (from config)
# Import from config
from config.runtime import TELEGRAM_TOKENS
BOT_TOKEN = TELEGRAM_TOKENS.get("VALSTANBOT")
# You need to get your chat ID by sending /start to your bot
# and checking https://api.telegram.org/bot<TOKEN>/getUpdates
CHAT_ID = None  # Set your chat ID here


async def test_health_check():
    """Test health checker"""
    print("=" * 60)
    print("🧪 Testing Health Checker")
    print("=" * 60)
    
    # Create health checker (without Telegram for now)
    checker = HealthChecker()
    
    # Run health check
    print("\n🔍 Running health check...")
    result = await checker.full_health_check()
    
    print("\n📊 Results:")
    print(f"  Status: {result['status']}")
    print(f"  Timestamp: {result['timestamp']}")
    
    print("\n  Database:")
    db = result['database']
    print(f"    Status: {db['status']}")
    if 'regions' in db:
        print(f"    Regions: {db['regions']}")
        print(f"    Communities: {db['communities']}")
        print(f"    Posts: {db['posts']}")
    
    print("\n  System Resources:")
    system = result['system']
    if 'cpu_percent' in system:
        print(f"    CPU: {system['cpu_percent']:.1f}%")
        print(f"    Memory: {system['memory']['percent']:.1f}% used")
        print(f"    Disk: {system['disk']['percent']:.1f}% used")
    
    if result.get('warnings'):
        print("\n  ⚠️  Warnings:")
        for warning in result['warnings']:
            print(f"    - {warning}")


async def test_telegram_notifier():
    """Test Telegram notifier"""
    print("\n" + "=" * 60)
    print("🧪 Testing Telegram Notifier")
    print("=" * 60)
    
    if not CHAT_ID:
        print("⚠️  CHAT_ID not set. Skipping Telegram test.")
        print("To get your chat ID:")
        print(f"1. Send /start to your bot")
        print(f"2. Visit: https://api.telegram.org/bot{BOT_TOKEN[:20]}..../getUpdates")
        print(f"3. Find your chat ID in the response")
        return
    
    # Create notifier
    notifier = TelegramNotifier(BOT_TOKEN, CHAT_ID)
    
    # Test simple message
    print("\n📤 Sending test message...")
    success = await notifier.send_message("🧪 Test message from SETKA monitoring system")
    
    if success:
        print("✅ Message sent successfully!")
    else:
        print("❌ Failed to send message")
    
    # Test error alert
    print("\n📤 Sending test error alert...")
    success = await notifier.send_error_alert(
        "This is a test error",
        module="TestModule",
        details="Testing error notification system"
    )
    
    if success:
        print("✅ Error alert sent successfully!")
    else:
        print("❌ Failed to send error alert")


async def main():
    """Main test function"""
    print("🚀 SETKA Monitoring System Test\n")
    
    try:
        # Test 1: Health check
        await test_health_check()
        
        # Test 2: Telegram notifications
        await test_telegram_notifier()
        
        print("\n" + "=" * 60)
        print("✅ All tests completed!")
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

