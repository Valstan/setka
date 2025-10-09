# SETKA - Quick Start Guide

**Обновлено:** 9 октября 2025  
**Версия:** 1.0.0-beta

---

## 🚀 Быстрый Старт

### 1. Запуск в Режиме Автоматизации

```bash
# Терминал 1: FastAPI сервер
cd /home/valstan/SETKA
source venv/bin/activate
uvicorn main:app --host 0.0.0.0 --port 8000

# Терминал 2: Celery worker и scheduler
cd /home/valstan/SETKA
./scripts/start_celery.sh
```

**Что происходит автоматически:**
- ⏱️ Каждые 5 минут: сканирование VK сообществ
- ⏱️ Каждые 2 минуты: AI анализ новых постов
- ⏱️ Каждый час (в :05): публикация одобренных постов
- ⏱️ Каждую минуту: health check
- ⏱️ Ежедневно в 3:30: очистка старых данных

### 2. Ручной Запуск Цикла

```bash
# Через CLI
python scripts/test_full_workflow.py

# Через API
curl -X POST http://localhost:8000/api/workflow/run-cycle \
  -H "Content-Type: application/json" \
  -d '{}'
```

### 3. Проверка Статуса

```bash
# Статус системы
curl http://localhost:8000/api/health

# Статус workflow
curl http://localhost:8000/api/workflow/status

# Проверка издателей
curl http://localhost:8000/api/workflow/publishers/status

# Статистика публикаций
curl http://localhost:8000/api/workflow/stats
```

---

## 📝 API Endpoints

### Health & Status
```bash
GET  /api/health                      # Здоровье системы
GET  /api/health/database             # Статус БД
```

### Regions
```bash
GET  /api/regions                     # Все регионы
GET  /api/regions/{code}              # Конкретный регион
```

### Communities
```bash
GET  /api/communities                 # Все сообщества
GET  /api/communities/region/{code}   # Сообщества региона
```

### Posts
```bash
GET  /api/posts                       # Все посты
GET  /api/posts/{id}                  # Конкретный пост
GET  /api/posts/region/{code}         # Посты региона
GET  /api/posts/status/{status}       # Посты по статусу
```

### Workflow (NEW!)
```bash
POST /api/workflow/run-cycle          # Запустить полный цикл
POST /api/workflow/publish            # Опубликовать пост
GET  /api/workflow/status             # Статус pipeline
GET  /api/workflow/publishers/status  # Статус издателей
GET  /api/workflow/schedule           # Расписание
GET  /api/workflow/stats              # Статистика
```

---

## 🛠️ Управление Celery

### Запуск
```bash
./scripts/start_celery.sh
```

### Остановка
```bash
./scripts/stop_celery.sh
```

### Просмотр Логов
```bash
# Worker logs
tail -f logs/celery_worker.log

# Beat logs
tail -f logs/celery_beat.log
```

### Проверка Статуса
```bash
# Проверить процессы
ps aux | grep celery

# Проверить PID файлы
cat logs/celery_worker.pid
cat logs/celery_beat.pid
```

---

## 📤 Публикация Контента

### Автоматическая Публикация

Система автоматически публикует одобренные посты каждый час.

### Ручная Публикация Поста

```bash
curl -X POST http://localhost:8000/api/workflow/publish \
  -H "Content-Type: application/json" \
  -d '{
    "post_id": 1,
    "platforms": ["vk", "telegram"],
    "region_code": "mi"
  }'
```

### Публикация Региона

```python
from modules.publisher.publisher import ContentPublisher
from config.config_secure import VK_TOKENS, TELEGRAM_TOKENS

# Initialize publisher
publisher = ContentPublisher(
    vk_token=VK_TOKENS['VALSTAN'],
    telegram_token=TELEGRAM_TOKENS['AFONYA']
)

# Publish approved posts for region
result = await publisher.publish_approved_posts(
    region_code='mi',
    platforms=['vk', 'telegram'],
    limit=5
)
```

---

## 🔍 Мониторинг

### Проверка Здоровья
```bash
curl http://localhost:8000/api/health | jq
```

### Статистика Pipeline
```bash
curl http://localhost:8000/api/workflow/status | jq
```

### Статус Издателей
```bash
curl http://localhost:8000/api/workflow/publishers/status | jq
```

---

## 🐛 Troubleshooting

### Celery не запускается

```bash
# Убедитесь что Redis работает
redis-cli ping

# Проверьте виртуальное окружение
source venv/bin/activate
which python

# Проверьте зависимости
pip install -r requirements.txt
```

### VK API ошибки

```bash
# Проверьте токены в config/config_secure.py
python -c "from config.config_secure import VK_TOKENS; print(VK_TOKENS)"

# Тест VK подключения
python scripts/test_vk_monitor.py
```

### База данных недоступна

```bash
# Проверьте PostgreSQL
sudo systemctl status postgresql

# Проверьте подключение
psql -U setka_user -d setka -h localhost
```

### Groq API ошибки (404)

Система использует fallback на keyword-based анализ. Это не критично, но можно:

```bash
# Проверить API ключ
python -c "from config.config_secure import GROQ_API_KEY; print(GROQ_API_KEY[:20])"

# Проверить endpoint в modules/ai_analyzer/groq_client.py
```

---

## 📊 Примеры Использования

### Полный Workflow Цикл

```python
from modules.scheduler.scheduler import ContentScheduler
from modules.publisher.publisher import ContentPublisher
from config.config_secure import VK_TOKENS, TELEGRAM_TOKENS, GROQ_API_KEY

# Initialize
tokens = [t for t in VK_TOKENS.values() if t]
publisher = ContentPublisher(
    vk_token=VK_TOKENS['VALSTAN'],
    telegram_token=TELEGRAM_TOKENS['AFONYA']
)
scheduler = ContentScheduler(tokens, GROQ_API_KEY, publisher)

# Run cycle
result = await scheduler.run_full_cycle()
print(f"New posts: {result['monitoring']['new_posts']}")
print(f"Analyzed: {result['analysis']['analyzed']}")
print(f"Published: {result['publishing']['published']}")
```

### Только Мониторинг

```python
from modules.vk_monitor.monitor import VKMonitor
from config.config_secure import VK_TOKENS

tokens = [t for t in VK_TOKENS.values() if t]
monitor = VKMonitor(tokens)

# Scan region
result = await monitor.scan_region('mi')
print(f"New posts: {result['new_posts']}")
```

### Только Анализ

```python
from modules.ai_analyzer.analyzer import PostAnalyzer
from config.config_secure import GROQ_API_KEY

analyzer = PostAnalyzer(GROQ_API_KEY)

# Analyze new posts
result = await analyzer.analyze_new_posts(limit=10)
print(f"Analyzed: {result['analyzed']}")
print(f"Approved: {result['approved']}")
```

---

## 🔐 Безопасность

### Важные файлы (не коммитить в Git!)

- `config/config_secure.py` - все токены и пароли
- `logs/*.log` - могут содержать чувствительные данные
- `*.pid` - файлы процессов

### .gitignore

Убедитесь что следующие файлы в `.gitignore`:
```
config/config_secure.py
*.log
*.pid
__pycache__/
venv/
backup/
```

---

## 📚 Дополнительная Документация

- `PROJECT_STATUS.md` - текущий статус проекта
- `docs/SESSION_3_COMPLETE.md` - детали последней сессии
- `docs/DEVELOPMENT_PLAN.md` - план разработки
- `docs/QUICK_START_FOR_AI.md` - для AI ассистентов

---

## 🆘 Помощь

При проблемах:

1. Проверьте логи: `logs/app.log`, `logs/celery_worker.log`
2. Проверьте статус сервисов: PostgreSQL, Redis
3. Проверьте API: `http://localhost:8000/docs`
4. Запустите тесты: `python scripts/test_full_workflow.py`

---

**Система готова к работе! 🚀**

