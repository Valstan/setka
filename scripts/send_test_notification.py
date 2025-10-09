#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Send test Telegram notification
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.monitoring.telegram_notifier import TelegramNotifier

# Configuration
BOT_TOKEN = "489021673:AAH7QDGmqzOMgT0W_wINvzWC1ihfljuFAKI"
CHAT_ID = "352096813"  # Валентин Савиных


async def send_test_messages():
    """Send test messages to Telegram"""
    print("=" * 70)
    print("📱 Testing Telegram Notifications")
    print("=" * 70)
    print(f"\n✅ Bot Token: {BOT_TOKEN[:20]}...")
    print(f"✅ Chat ID: {CHAT_ID}")
    print(f"✅ Recipient: Валентин Савиных (@Valentin_Savinykh)")
    
    notifier = TelegramNotifier(BOT_TOKEN, CHAT_ID)
    
    # Test 1: Simple message
    print("\n📤 Test 1: Sending simple message...")
    success = await notifier.send_message(
        "🎉 <b>SETKA - Тестовое сообщение</b>\n\n"
        "Если вы видите это - система уведомлений работает!\n\n"
        "✅ Telegram интеграция настроена успешно! 🚀"
    )
    
    if success:
        print("   ✅ Сообщение отправлено!")
    else:
        print("   ❌ Ошибка отправки")
    
    await asyncio.sleep(1)
    
    # Test 2: Error alert
    print("\n📤 Test 2: Sending error alert...")
    success = await notifier.send_error_alert(
        "Тестовая ошибка для проверки системы",
        module="TestModule",
        details="Это тестовое уведомление об ошибке.\nВсё работает корректно!"
    )
    
    if success:
        print("   ✅ Error alert отправлен!")
    else:
        print("   ❌ Ошибка отправки")
    
    await asyncio.sleep(1)
    
    # Test 3: Success notification
    print("\n📤 Test 3: Sending success notification...")
    success = await notifier.send_success_notification(
        "VK мониторинг успешно загрузил 10 постов из сообществ!",
        module="VKMonitor"
    )
    
    if success:
        print("   ✅ Success notification отправлено!")
    else:
        print("   ❌ Ошибка отправки")
    
    await asyncio.sleep(1)
    
    # Test 4: Stats report
    print("\n📤 Test 4: Sending stats report...")
    success = await notifier.send_stats_report({
        "regions": 14,
        "communities": 2,
        "posts": 10,
        "new_posts": 10,
        "analyzed": 0,
        "published": 0
    })
    
    if success:
        print("   ✅ Stats report отправлен!")
    else:
        print("   ❌ Ошибка отправки")
    
    print("\n" + "=" * 70)
    print("✅ Все тесты завершены!")
    print("=" * 70)
    print("\n💬 Проверьте Telegram - должно прийти 4 сообщения!")


if __name__ == "__main__":
    asyncio.run(send_test_messages())

