# START HERE (AI / новая сессия)

Если что-то в документации расходится с кодом — приоритет у кода и реальных конфигов сервера.

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

## 5) “Ребилд” backend и модулей

Если менялись зависимости или окружение:

```bash
cd /home/valstan/SETKA
source venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart setka setka-celery-worker setka-celery-beat
```

Если менялся только код — достаточно перезапуска сервисов.

## 6) Быстрые ориентиры

- UI: `/`, `/regions`, `/posts`, `/communities`, `/notifications`, `/tokens`, `/publisher`, `/monitoring`, `/schedule`
- Метрики: `/metrics`
- Nginx: редактировать `config/setka.conf.editable`, применять `scripts/apply_nginx_config.sh`


