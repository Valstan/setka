# 🤖 Быстрый старт для AI ассистентов

**Этот файл для нейросетей, помогающих в разработке SETKA**

---

## 📍 Текущая локация проекта

```
/home/valstan/SETKA/
```

---

## 🔑 Важная информация

### Пароли и доступы:
- **Sudo пароль:** [REDACTED] (настроен sudo без пароля)
- **PostgreSQL:**
  - Database: `setka`
  - User: `setka_user`
  - Password: [REDACTED - see config/config_secure.py]
  - Host: `localhost:5432`

### Токены (в config/config_secure.py):
- VK токены: VK_TOKEN_VALSTAN, VK_TOKEN_OLGA, VK_TOKEN_VITA
- Telegram: TELEGA_TOKEN_VALSTANBOT, TELEGA_TOKEN_AFONYA
- MongoDB старого проекта: [REDACTED - see config/config_secure.py]

### Домен:
- **URL:** 3931b3fe50ab.vps.myjino.ru
- **SSL:** Валиден до 2026-01-06 (автообновление настроено)

---

## 🏃 Команды для работы

### Активация окружения:
```bash
cd /home/valstan/SETKA
source venv/bin/activate
```

### Запуск приложения:
```bash
# FastAPI приложение
python main.py

# В фоне с логами
nohup python main.py > logs/uvicorn.log 2>&1 &

# Остановить
pkill -f "python main.py"
```

### Работа с БД:
```bash
# Подключиться к БД
sudo -u postgres psql -d setka

# Просмотреть таблицы
sudo -u postgres psql -d setka -c "\dt"

# Просмотреть регионы
sudo -u postgres psql -d setka -c "SELECT code, name FROM regions;"

# Посчитать посты
sudo -u postgres psql -d setka -c "SELECT COUNT(*) FROM posts;"
```

### Тестирование:
```bash
# VK Monitor
python scripts/test_vk_monitor.py

# Monitoring
python scripts/test_monitoring.py

# Бэкап
scripts/backup_database.sh
```

### Проверка API:
```bash
# Health check
curl http://localhost:8000/api/health/

# Регионы
curl http://localhost:8000/api/regions/

# Посты
curl http://localhost:8000/api/posts/

# Swagger UI
http://localhost:8000/docs
```

---

## 📚 Документация проекта

**Обязательно прочитай перед работой:**

1. **README.md** - общее описание проекта
2. **DEVELOPMENT_PLAN.md** - план разработки (10 этапов)
3. **DEVELOPMENT_HISTORY.md** - история изменений
4. **SESSION_2_COMPLETE.md** - итоги последней сессии
5. **TECH_STACK_PROPOSAL.md** - технологический стек
6. **OLD_PROJECT_SUMMARY.md** - анализ старого Postopus
7. **AI_SOLUTION.md** - решение по AI компоненту

---

## 🎯 Текущий статус (обновлено 8 окт 2025)

### ✅ Готово (65%):
- Инфраструктура (100%)
- База данных PostgreSQL (100%)
- VK мониторинг (90%)
- AI анализатор Groq (80%)
- Система мониторинга (90%)
- Автобэкапы (100%)
- SSL/HTTPS (100%)
- FastAPI backend (70%)

### ⏳ В разработке:
- Publisher модуль (0%)
- Планировщик Celery (0%)
- Web интерфейс React (0%)
- Интеграция модулей (20%)

---

## 🔧 Технологии

**Backend:**
- Python 3.12 + FastAPI
- PostgreSQL 17.6
- Redis 7.4.1
- Celery (в планах)

**AI:**
- Groq API (основное)
- Keyword analysis (fallback)
- Ollama + Qwen2.5 1.5B (для будущего, нужно 4GB RAM)

**Monitoring:**
- Python-telegram-bot
- Psutil
- Health checks

**VK:**
- vk-api
- aiohttp, httpx

---

## ⚠️ Известные ограничения

1. **RAM:** 1.5 GB (доступно ~900 MB)
   - Локальные AI модели не влезают
   - Используем Groq API вместо этого

2. **Disk:** 9.8 GB (свободно 2.6 GB)
   - Достаточно для текущего этапа
   - При масштабировании нужен апгрейд

3. **VK токены:**
   - Некоторые токены невалидны
   - Нужно обновить в config/config_secure.py

---

## 🐛 Если что-то не работает

### FastAPI не отвечает:
```bash
# Проверить процесс
ps aux | grep "python main.py"

# Проверить логи
tail -f logs/uvicorn.log
tail -f logs/app.log

# Перезапустить
pkill -f "python main.py"
cd /home/valstan/SETKA && source venv/bin/activate && python main.py
```

### База данных недоступна:
```bash
# Проверить PostgreSQL
sudo systemctl status postgresql@17-main

# Перезапустить
sudo systemctl restart postgresql@17-main

# Проверить подключение
sudo -u postgres psql -d setka -c "SELECT 1;"
```

### SSL не работает:
```bash
# Проверить сертификат
sudo certbot certificates

# Обновить вручную
sudo certbot renew

# Проверить Nginx
sudo nginx -t
sudo systemctl restart nginx
```

---

## 💾 Бэкапы

**Автоматически:** Каждый день в 3:00 AM  
**Расположение:** `/home/valstan/SETKA/backup/`  
**Формат:** `setka_backup_YYYYMMDD_HHMMSS.sql.gz`

**Восстановление:**
```bash
gunzip setka_backup_20251008_162528.sql.gz
sudo -u postgres psql -d setka < setka_backup_20251008_162528.sql
```

---

## 📞 Telegram боты

**Для уведомлений:**
- Token: [REDACTED - see config/config_secure.py]
- Нужен chat_id (получить отправив /start)

**Для поддержки:**
- Token: [REDACTED - see config/config_secure.py]

---

## 🚀 Быстрые задачи

### Добавить сообщество VK:
```python
# В Python shell
from database.models import Community
from database.connection import AsyncSessionLocal
# ... create community
```

### Запустить сканирование:
```python
from modules.vk_monitor.monitor import VKMonitor
# ... (см. test_vk_monitor.py)
```

### Проанализировать пост:
```python
from modules.ai_analyzer.analyzer import PostAnalyzer
# ... (см. analyzer.py)
```

---

**Всегда обращайся к этому файлу при начале новой сессии!**

