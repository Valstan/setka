#!/usr/bin/env python3
"""
Тестовый скрипт для диагностики системы уведомлений

Проверяет:
1. Подключение к VK API
2. Проверку suggested posts
3. Проверку unread messages
4. Сохранение в Redis
5. Получение из Redis
"""
import sys
import os
import asyncio

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.config_secure import VK_TOKENS
from modules.notifications.vk_suggested_checker import VKSuggestedChecker
from modules.notifications.vk_messages_checker import VKMessagesChecker
from modules.notifications.unified_checker import UnifiedNotificationsChecker
from modules.notifications.storage import NotificationsStorage


async def test_notifications():
    """Полный тест системы уведомлений"""
    
    print("="*70)
    print("🧪 ТЕСТ СИСТЕМЫ УВЕДОМЛЕНИЙ SETKA")
    print("="*70)
    
    # 1. Проверка токена
    print("\n1️⃣ Проверка VK токена...")
    vk_token = VK_TOKENS.get("VALSTAN")
    if not vk_token:
        print("❌ VK токен не найден!")
        return
    print(f"✅ VK токен найден (длина: {len(vk_token)})")
    
    # 2. Тестовые группы (главные группы регионов)
    print("\n2️⃣ Подготовка тестовых групп...")
    test_groups = [
        {
            'region_id': 1,
            'region_name': 'МАЛМЫЖ - ИНФО',
            'region_code': 'mi',
            'vk_group_id': -158787639
        },
        {
            'region_id': 12,
            'region_name': 'ЛЕБЯЖЬЕ - ИНФО',
            'region_code': 'leb',
            'vk_group_id': -170437443
        },
        {
            'region_id': 2,
            'region_name': 'НОЛИНСК - ИНФО',
            'region_code': 'nolinsk',
            'vk_group_id': -179306667
        }
    ]
    print(f"✅ Подготовлено {len(test_groups)} тестовых групп")
    
    # 3. Тест VKSuggestedChecker
    print("\n3️⃣ Тест проверки предложенных постов...")
    try:
        suggested_checker = VKSuggestedChecker(vk_token)
        
        for group in test_groups:
            print(f"\n   Проверка {group['region_name']} ({group['vk_group_id']})...")
            result = suggested_checker.check_suggested_posts(group['vk_group_id'])
            
            if 'error' in result:
                print(f"   ⚠️  Ошибка: {result['error']}")
            elif result['has_suggested']:
                print(f"   ✅ Найдено {result['count']} предложенных постов")
                print(f"      URL: {result['url']}")
            else:
                print(f"   ℹ️  Нет предложенных постов")
        
        print("\n✅ Тест VKSuggestedChecker завершён")
    except Exception as e:
        print(f"\n❌ Ошибка в VKSuggestedChecker: {e}")
    
    # 4. Тест VKMessagesChecker
    print("\n4️⃣ Тест проверки непрочитанных сообщений...")
    try:
        messages_checker = VKMessagesChecker(vk_token)
        
        for group in test_groups:
            print(f"\n   Проверка {group['region_name']} ({group['vk_group_id']})...")
            result = messages_checker.check_unread_messages(group['vk_group_id'])
            
            if 'error' in result:
                error_code = result.get('error_code', 'unknown')
                print(f"   ⚠️  Ошибка (код {error_code}): {result['error']}")
                if error_code == 15:
                    print(f"      💡 Нет доступа к сообщениям. Токен должен иметь права на messages")
                elif error_code == 917:
                    print(f"      💡 Сообщения отключены в группе")
            elif result['has_unread']:
                print(f"   ✅ Найдено {result['unread_count']} непрочитанных сообщений")
                print(f"      URL: {result['url']}")
                print(f"      Всего диалогов: {result['total_conversations']}")
            else:
                print(f"   ℹ️  Нет непрочитанных сообщений")
        
        print("\n✅ Тест VKMessagesChecker завершён")
    except Exception as e:
        print(f"\n❌ Ошибка в VKMessagesChecker: {e}")
    
    # 5. Тест UnifiedChecker (полная проверка)
    print("\n5️⃣ Тест объединённой проверки (UnifiedChecker)...")
    try:
        unified_checker = UnifiedNotificationsChecker(vk_token)
        result = await unified_checker.check_all(test_groups)
        
        print(f"\n   📊 Результаты:")
        print(f"      📝 Предложенных постов: {result['suggested_count']}")
        print(f"      💬 Непрочитанных сообщений: {result['messages_count']}")
        print(f"      📬 Всего уведомлений: {result['total_count']}")
        
        if result['suggested_count'] > 0:
            print(f"\n   Группы с предложенными постами:")
            for notif in result['suggested_posts']:
                print(f"      • {notif['region_name']}: {notif['suggested_count']} пост(ов)")
        
        if result['messages_count'] > 0:
            print(f"\n   Группы с непрочитанными сообщениями:")
            for notif in result['unread_messages']:
                print(f"      • {notif['region_name']}: {notif['unread_count']} сообщ.")
        
        print("\n✅ Тест UnifiedChecker завершён")
        
    except Exception as e:
        print(f"\n❌ Ошибка в UnifiedChecker: {e}")
        import traceback
        traceback.print_exc()
    
    # 6. Тест Redis Storage
    print("\n6️⃣ Тест Redis хранилища...")
    try:
        storage = NotificationsStorage()
        
        # Получить сохранённые данные
        all_notifs = storage.get_all_notifications()
        
        print(f"\n   📦 Данные в Redis:")
        print(f"      📝 Предложенных постов: {all_notifs['suggested_count']}")
        print(f"      💬 Непрочитанных сообщений: {all_notifs['messages_count']}")
        print(f"      📬 Всего: {all_notifs['total_count']}")
        
        if all_notifs['timestamp']:
            print(f"      🕐 Последнее обновление: {all_notifs['timestamp']}")
        
        print("\n✅ Тест Redis Storage завершён")
        
    except Exception as e:
        print(f"\n❌ Ошибка в Redis Storage: {e}")
    
    # Итоги
    print("\n" + "="*70)
    print("✅ ТЕСТ ЗАВЕРШЁН")
    print("="*70)
    
    print("\n💡 Рекомендации:")
    print("   1. Если есть ошибки с кодом 15 или 917 - токен не имеет прав на messages")
    print("   2. Для полного функционала получите токен с правами: groups,messages")
    print("   3. Suggested posts должны работать с текущим токеном")
    print("   4. Проверьте данные в Redis: redis-cli get 'setka:notifications:*'")


if __name__ == "__main__":
    asyncio.run(test_notifications())

