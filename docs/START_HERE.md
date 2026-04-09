# START HERE (AI / новая сессия)

Если что-то в документации расходится с кодом — приоритет у кода и реальных конфигов сервера.

---

## ⚠️ ОБЯЗАТЕЛЬНО: Начало каждой сессии разработки

**Перед любой работой AI должен синхронизироваться:**

```bash
cd /home/valstan/SETKA
git status
git fetch origin
git pull origin $(git branch --show-current)
git log --oneline -5
```

Это гарантирует актуальность кода и предотвращает конфликты.

---

## 0) VPS и окружение

- ОС: Ubuntu 24.04.3 LTS
- Проект: `/home/valstan/SETKA`
- `sudo`: без пароля, можно использовать админ-команды
- Переменные окружения: `/etc/setka/setka.env` (секреты только там, не коммитить)
- Systemd-сервисы: `setka`, `setka-celery-worker`, `setka-celery-beat`
- FastAPI слушает `127.0.0.1:8000`, наружу проксирует Nginx

## 1) Источники истины в коде

- `main.py` — FastAPI приложение, подключение роутов, `/metrics`
- `web/api/*` — фактические API endpoints
- `tasks/celery_app.py` — расписания Celery (beat)
- `tasks/*.py` — фоновые задачи
- `database/models.py` — структура БД
- `database/connection.py`, `config/runtime.py` — конфиг и env

## 2) Быстрая проверка живости

```bash
systemctl status setka setka-celery-worker setka-celery-beat
curl http://127.0.0.1:8000/api/health/
```

Swagger: `http://127.0.0.1:8000/docs`

## 3) Production: перезапуск сервисов

```bash
sudo systemctl restart setka setka-celery-worker setka-celery-beat
```

Логи: `/home/valstan/SETKA/logs/` (есть logrotate).

## 4) Dev/ручной запуск

FastAPI:

```bash
cd /home/valstan/SETKA
source venv/bin/activate
python main.py
```

Celery:

```bash
cd /home/valstan/SETKA
source venv/bin/activate
./scripts/start_celery.sh
```

Остановка:

```bash
./scripts/stop_celery.sh
```

## 5) "Ребилд" backend и модулей

Если менялись зависимости или окружение:

```bash
cd /home/valstan/SETKA
source venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart setka setka-celery-worker setka-celery-beat
```

Если менялся только код — достаточно перезапуска сервисов.

## 6) Тестирование (НОВОЕ!)

Проект использует pytest для unit-тестирования. **Все новые модули должны быть покрыты тестами.**

```bash
# Запуск всех unit-тестов
cd /home/valstan/SETKA
source venv/bin/activate
pytest tests/ -v

# С покрытием
pytest tests/ --cov=modules --cov=tasks

# Конкретный модуль
pytest tests/test_filters/ -v
```

Подробнее: [`docs/TESTING.md`](TESTING.md)

### Pre-commit хуки

```bash
# Установка
pip install pre-commit
pre-commit install

# Запуск вручную
pre-commit run --all-files
```

## 7) Рабочий процесс разработки

### При добавлении функциональности:
1. Изучить существующую архитектуру (`modules/`, `tasks/`, `web/api/`)
2. Создать модуль с типизацией и валидацией
3. **Написать тесты (обязательно!)**
4. Проверить self-review чек-лист (см. [`AI_DEV_GUIDE.md`](AI_DEV_GUIDE.md))
5. Обновить документацию если нужно
6. **Обновить [`docs/DEV_HISTORY.md`](DEV_HISTORY.md)**
7. Запустить тесты: `pytest tests/ -q`
8. Запустить pre-commit: `pre-commit run --all-files`

### При рефакторинге:
1. Убедиться что тесты проходят
2. Сохранить обратную совместимость
3. Актуализировать тесты под изменения
4. **Обновить [`docs/DEV_HISTORY.md`](DEV_HISTORY.md)**

## 8) Быстрые ориентиры

- UI: `/`, `/regions`, `/posts`, `/communities`, `/notifications`, `/tokens`, `/publisher`, `/monitoring`, `/schedule`
- Метрики: `/metrics`
- Nginx: редактировать `config/setka.conf.editable`, применять `scripts/apply_nginx_config.sh`

## 9) Чек-лист завершения задачи

Перед коммитом AI должен проверить:

- [ ] Код соответствует требованиям типизации
- [ ] Написаны/обновлены тесты для новых/изменённых модулей
- [ ] `pytest tests/ -q` проходит
- [ ] `pre-commit run --all-files` проходит
- [ ] Секреты не захардкожены
- [ ] **Обновлён `docs/DEV_HISTORY.md`**
- [ ] Синхронизация с git: `git pull` → `git add` → `git commit` → `git push`

---

*Последнее обновление: 2026-04-08*
