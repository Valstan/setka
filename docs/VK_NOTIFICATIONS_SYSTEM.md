# 📬 Система уведомлений VK для SETKA

**Комплексный мониторинг активности в главных группах регионов**

---

## 🎯 Что мониторится

Система проверяет ДВА типа уведомлений:

### 1. 📝 Предложенные посты (Suggested Posts)
- Посты, предложенные пользователями в группы
- Ожидают модерации администратором

### 2. 💬 Непрочитанные сообщения (Unread Messages)
- Сообщения от пользователей группе
- Требуют ответа администратора

---

## 🏗️ Архитектура

```
Celery Beat (hourly)
    ↓
UnifiedNotificationsChecker
    ├→ VKSuggestedChecker    → Проверяет suggested posts
    ├→ VKMessagesChecker     → Проверяет unread messages
    ↓
NotificationsStorage (Redis)
    ├→ suggested_posts (TTL 24h)
    └→ unread_messages (TTL 24h)
    ↓
Telegram Bot → Уведомление со ссылкой на кабинет
```

---

## 📋 Компоненты

### 1. VKSuggestedChecker

**Файл:** `modules/notifications/vk_suggested_checker.py`

**Функция:** Проверяет предложенные посты

**VK API:** `wall.get` с `filter='suggests'`

**Пример:**
```python
from modules.notifications.vk_suggested_checker import VKSuggestedChecker

checker = VKSuggestedChecker(vk_token)
result = checker.check_suggested_posts(-123456)

print(f"Suggested: {result['count']}")
print(f"URL: {result['url']}")
```

---

### 2. VKMessagesChecker (НОВОЕ!)

**Файл:** `modules/notifications/vk_messages_checker.py`

**Функция:** Проверяет непрочитанные сообщения

**VK API:** `messages.getConversations` с `filter='unread'`

**Пример:**
```python
from modules.notifications.vk_messages_checker import VKMessagesChecker

checker = VKMessagesChecker(vk_token)
result = checker.check_unread_messages(-123456)

print(f"Unread: {result['unread_count']}")
print(f"URL: {result['url']}")  # https://vk.com/gim123456
```

---

### 3. UnifiedNotificationsChecker (НОВОЕ!)

**Файл:** `modules/notifications/unified_checker.py`

**Функция:** Объединённая проверка обоих типов

**Пример:**
```python
from modules.notifications.unified_checker import UnifiedNotificationsChecker

checker = UnifiedNotificationsChecker(vk_token)

# Проверить все регионы
result = await checker.check_all(region_groups)

print(f"Suggested: {result['suggested_count']}")
print(f"Messages: {result['messages_count']}")
print(f"Total: {result['total_count']}")

# Отправить Telegram уведомление
await checker.send_telegram_notification(
    bot_token=telegram_token,
    chat_id=chat_id,
    notifications_data=result,
    dashboard_url="https://example.com/notifications"
)
```

---

### 4. NotificationsStorage

**Файл:** `modules/notifications/storage.py`

**Функция:** Хранение уведомлений в Redis (TTL 24h)

**Методы:**

```python
from modules.notifications.storage import NotificationsStorage

storage = NotificationsStorage()

# Сохранить suggested posts
storage.save_notifications(notifications, 'suggested_posts')

# Сохранить messages
storage.save_notifications(notifications, 'unread_messages')

# Получить все
all_notifs = storage.get_all_notifications()
print(f"Total: {all_notifs['total_count']}")
print(f"Suggested: {all_notifs['suggested_count']}")
print(f"Messages: {all_notifs['messages_count']}")

# Очистить suggested
storage.clear_notifications('suggested_posts')

# Очистить messages
storage.clear_notifications('unread_messages')

# Очистить всё
storage.clear_notifications()
```

---

## 🔄 Celery Task

**Файл:** `tasks/notification_tasks.py`

**Task:** `check_vk_notifications`

**Расписание:** Каждый час (3600 секунд)

**Что делает:**
1. Получает список главных групп регионов из БД
2. Проверяет suggested posts для каждой группы
3. Проверяет unread messages для каждой группы
4. Сохраняет результаты в Redis
5. Если есть уведомления → отправляет в Telegram

**Конфигурация в `celery_app.py`:**
```python
'check-vk-notifications': {
    'task': 'tasks.notification_tasks.check_vk_notifications',
    'schedule': 3600.0,  # Каждый час
    'options': {
        'expires': 3500,
    }
},
```

---

## 🌐 API Endpoints

### GET /api/notifications/

**Описание:** Получить все уведомления

**Ответ:**
```json
{
    "suggested_posts": [
        {
            "region_name": "МАЛМЫЖ - ИНФО",
            "suggested_count": 3,
            "url": "https://vk.com/club158787639",
            ...
        }
    ],
    "unread_messages": [
        {
            "region_name": "МАЛМЫЖ - ИНФО",
            "unread_count": 5,
            "url": "https://vk.com/gim158787639",
            ...
        }
    ],
    "total_count": 2,
    "suggested_count": 1,
    "messages_count": 1
}
```

---

### GET /api/notifications/suggested

**Описание:** Только предложенные посты

---

### GET /api/notifications/messages

**Описание:** Только непрочитанные сообщения

---

### POST /api/notifications/check-now

**Описание:** Запустить проверку вручную (не ждать Celery)

**Ответ:**
```json
{
    "success": true,
    "total_count": 8,
    "suggested_count": 3,
    "messages_count": 5,
    "message": "Found 3 suggested posts and 5 unread messages"
}
```

**Эффект:** 
- Проверяет все группы
- Сохраняет в Redis
- Отправляет Telegram уведомление (если есть новые)

---

### DELETE /api/notifications/

**Описание:** Очистить все уведомления

---

## 📱 Telegram Уведомления

### Формат сообщения:

```
📬 Новые уведомления SETKA

📝 Предложенных постов: 3
  • МАЛМЫЖ - ИНФО: 2 пост(ов)
  • КИЛЬМЕЗЬ - ИНФО: 1 пост(ов)

💬 Непрочитанных сообщений: 5
  • МАЛМЫЖ - ИНФО: 3 сообщ.
  • СОВЕТСК - ИНФО: 2 сообщ.

🔗 Открыть кабинет уведомлений
🕐 Проверено: 19:30
```

**Настройки:**
- Bot token: `TELEGRAM_TOKENS['VALSTANBOT']`
- Chat ID: `TELEGRAM_ALERT_CHAT_ID`
- URL кабинета: `https://{SERVER['domain']}/notifications`

---

## 🛠️ Настройка

### 1. VK токен

Токен должен иметь права:
- ✅ **Управление сообществом** (для suggested posts)
- ⚠️ **Messages** (для непрочитанных сообщений)

**Где:** `config/config_secure.py` → `VK_TOKENS['VALSTAN']`

**Примечание:** Если токен без прав на messages, система продолжит работать, просто messages_count будет 0.

---

### 2. Telegram

**Настроить в `config/config_secure.py`:**

```python
TELEGRAM_TOKENS = {
    "VALSTANBOT": "YOUR_BOT_TOKEN"
}

TELEGRAM_ALERT_CHAT_ID = "YOUR_CHAT_ID"

SERVER = {
    "domain": "3931b3fe50ab.vps.myjino.ru"
}
```

---

### 3. Celery

**Запустить Celery worker и beat:**

```bash
cd /home/valstan/SETKA
source venv/bin/activate

# Terminal 1: Worker
celery -A celery_app worker --loglevel=info

# Terminal 2: Beat (scheduler)
celery -A celery_app beat --loglevel=info
```

**Или через systemd:** (см. предыдущую документацию)

---

## 🧪 Тестирование

### Ручная проверка через API:

```bash
# Проверить вручную
curl -X POST http://localhost:8000/api/notifications/check-now

# Посмотреть результат
curl http://localhost:8000/api/notifications/

# Только suggested
curl http://localhost:8000/api/notifications/suggested

# Только messages
curl http://localhost:8000/api/notifications/messages
```

---

### Через Python:

```python
import asyncio
from modules.notifications.unified_checker import UnifiedNotificationsChecker
from config.runtime import VK_TOKENS

async def test():
    checker = UnifiedNotificationsChecker(VK_TOKENS["VALSTAN"])
    
    region_groups = [
        {
            'region_id': 1,
            'region_name': 'МАЛМЫЖ - ИНФО',
            'region_code': 'mi',
            'vk_group_id': -158787639
        }
    ]
    
    result = await checker.check_all(region_groups)
    print(f"Total: {result['total_count']}")
    print(f"Suggested: {result['suggested_count']}")
    print(f"Messages: {result['messages_count']}")

asyncio.run(test())
```

---

## 📊 Мониторинг

### Логи:

```bash
# Celery logs
tail -f logs/celery.log

# App logs
tail -f logs/app.log | grep "notifications"

# Проверить последнюю проверку
grep "VK notifications check" logs/app.log | tail -5
```

---

### Redis:

```bash
# Посмотреть ключи уведомлений
redis-cli keys "setka:notifications:*"

# Посмотреть suggested
redis-cli get "setka:notifications:suggested_posts"

# Посмотреть messages  
redis-cli get "setka:notifications:unread_messages"
```

---

## ⚙️ Конфигурация

### Изменить частоту проверки:

**В `celery_app.py`:**

```python
'check-vk-notifications': {
    'task': 'tasks.notification_tasks.check_vk_notifications',
    'schedule': 1800.0,  # Изменить на 30 минут (1800 секунд)
},
```

---

### Изменить формат Telegram сообщения:

**В `modules/notifications/unified_checker.py`:**

Метод `send_telegram_notification()` - изменить `message_parts`

---

## 🔍 Troubleshooting

### Проблема: "No access to messages"

**Причина:** Токен не имеет прав на messages

**Решение:**
1. Получить новый токен с правами на messages
2. Или оставить как есть - suggested posts будут работать

---

### Проблема: "No regions with VK groups found"

**Причина:** В таблице regions нет vk_group_id

**Решение:**
```sql
-- Добавить vk_group_id для региона
UPDATE regions 
SET vk_group_id = -158787639 
WHERE code = 'mi';
```

---

### Проблема: Telegram не отправляет

**Проверить:**
1. `TELEGRAM_TOKENS['VALSTANBOT']` - токен бота
2. `TELEGRAM_ALERT_CHAT_ID` - chat ID
3. Бот добавлен в чат
4. Логи: `grep "Telegram" logs/app.log`

---

## 📈 Метрики

**Добавлены в Prometheus:**

```promql
# Уведомлений checked за час
rate(setka_notifications_checked_total[1h])

# Suggested posts found
rate(setka_notifications_suggested_total[1h])

# Messages found  
rate(setka_notifications_messages_total[1h])
```

*(Если добавить metrics в checker)*

---

## 🚀 Quick Start

### Запустить проверку вручную:

```bash
curl -X POST http://localhost:8000/api/notifications/check-now
```

### Посмотреть результат:

```bash
curl http://localhost:8000/api/notifications/ | jq
```

### Ожидаемый результат:

```json
{
  "suggested_posts": [...],
  "unread_messages": [...],
  "total_count": 8,
  "suggested_count": 3,
  "messages_count": 5,
  "timestamp": "2025-10-11T19:30:00"
}
```

---

## ✅ Checklist

- [x] VKMessagesChecker создан
- [x] Storage расширен для messages
- [x] UnifiedChecker объединяет оба типа
- [x] Celery task создан
- [x] Beat schedule добавлен (hourly)
- [x] API endpoints обновлены
- [x] Telegram уведомления реализованы
- [x] Документация создана

---

## 📝 Changelog

**11 октября 2025:**
- ✅ Добавлен VKMessagesChecker
- ✅ Расширен NotificationsStorage
- ✅ Создан UnifiedNotificationsChecker
- ✅ Telegram уведомления с обоими типами
- ✅ API endpoints обновлены
- ✅ Celery task scheduled hourly

---

**Автор:** AI Assistant (Claude Sonnet 4.5)  
**Дата:** 11 октября 2025  
**Версия:** 1.0

🎉 **Система уведомлений готова к использованию!**

