#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Quick test - send message to Telegram
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.monitoring.telegram_notifier import TelegramNotifier

# Конфигурация
BOT_TOKEN = "489021673:AAH7QDGmqzOMgT0W_wINvzWC1ihfljuFAKI"
CHAT_ID = input("Введите ваш chat_id (число): ").strip()

async def test():
    """Test sending message"""
    if not CHAT_ID:
        print("❌ Chat ID не указан!")
        return
    
    print(f"\n📤 Отправляю тестовое сообщение в Telegram...")
    print(f"   Chat ID: {CHAT_ID}")
    
    notifier = TelegramNotifier(BOT_TOKEN, CHAT_ID)
    
    # Test 1: Simple message
    success = await notifier.send_message("🧪 Тест из SETKA!\n\nЕсли вы видите это сообщение - всё работает! ✅")
    
    if success:
        print("✅ Сообщение отправлено!")
    else:
        print("❌ Ошибка отправки. Проверьте chat_id.")
    
    # Test 2: Error alert
    await asyncio.sleep(1)
    await notifier.send_error_alert(
        "Это тестовая ошибка",
        module="TestModule",
        details="Проверка системы уведомлений"
    )

if __name__ == "__main__":
    asyncio.run(test())

