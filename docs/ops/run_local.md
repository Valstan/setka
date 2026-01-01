# Запуск (локально / на сервере)

## FastAPI

### Dev

```bash
cd /home/valstan/SETKA
source venv/bin/activate
python main.py
```

Примечание: `python main.py` в `__main__` запускает uvicorn с `reload=True` (удобно для разработки, но дороже по CPU).

Проверка:

```bash
curl http://localhost:8000/api/health/
```

Swagger:

- `http://localhost:8000/docs`

### Production (рекомендуемо на сервере)

```bash
cd /home/valstan/SETKA
source venv/bin/activate
uvicorn main:app --host 127.0.0.1 --port 8000 --workers 1
```

### Логи

- `logs/app.log`

## Celery

Запуск worker+beat:

```bash
cd /home/valstan/SETKA
./scripts/start_celery.sh
```

Остановка:

```bash
./scripts/stop_celery.sh
```

Логи:

- `logs/celery_worker.log`
- `logs/celery_beat.log`

Расписания:

- `tasks/celery_app.py` → `app.conf.beat_schedule`


