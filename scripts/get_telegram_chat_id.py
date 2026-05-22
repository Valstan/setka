#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Get Telegram chat_id for notifications
"""
import asyncio

import httpx

# Import from config
from config.runtime import TELEGRAM_TOKENS

BOT_TOKEN = TELEGRAM_TOKENS.get("VALSTANBOT")


async def get_chat_id():
    """Get chat ID from Telegram bot updates"""
    print("=" * 70)
    print("📱 Getting Telegram chat_id")
    print("=" * 70)
    print("\n⚠️  СНАЧАЛА отправьте /start вашему боту в Telegram!")
    print("   Бот: @valstanbot (или найдите по токену)\n")

    input("Нажмите Enter когда отправите /start боту...")

    print("\n🔍 Получаю обновления от бота...")

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates")

            if response.status_code == 200:
                data = response.json()

                if data["ok"] and data["result"]:
                    print("\n✅ Найдены сообщения:\n")

                    seen_chats = set()

                    for update in data["result"]:
                        if "message" in update:
                            chat = update["message"]["chat"]
                            chat_id = chat["id"]

                            if chat_id not in seen_chats:
                                seen_chats.add(chat_id)

                                print(f"   Chat ID: {chat_id}")
                                print(f"   Type: {chat.get('type', 'N/A')}")

                                if "username" in chat:
                                    print(f"   Username: @{chat['username']}")
                                if "first_name" in chat:
                                    print(f"   Name: {chat['first_name']}")

                                print(f"   {'-'*60}")

                    if seen_chats:
                        print("\n💡 Скопируйте один из chat_id выше и добавьте в конфиг!")
                    else:
                        print("\n⚠️  Сообщения не найдены. Отправьте /start боту и запустите снова.")
                else:
                    print("\n⚠️  Обновлений не найдено. Отправьте /start боту сначала.")
            else:
                print(f"\n❌ Ошибка API: {response.status_code}")
                print(response.text)

    except Exception as e:
        print(f"\n❌ Ошибка: {e}")


if __name__ == "__main__":
    asyncio.run(get_chat_id())
