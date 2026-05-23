---
description: Состояние Celery на проде — workers, beat, последние публикации, Redis cooldown по регионам.
argument-hint: [--errors — показать только ошибки за последние сутки, --region=<code> — фильтр по региону]
allowed-tools: Bash, Read, Grep, AskUserQuestion
---

# /celery — состояние Celery на проде

Покажет, что сейчас творится с фоновыми задачами Сетки: жив ли beat, нет ли стопора у worker, какие регионы публиковали недавно, какие ушли на cooldown.

## Шаг 1. Подтверждение прод-доступа

**Через `AskUserQuestion`** подтвердить SSH-доступ (если ещё не давали в этом чате).

## Шаг 2. Параллельные проверки

В одном SSH-блоке (через `ssh setka "..."`):

```bash
echo '=== systemd ==='
systemctl status setka-celery-worker setka-celery-beat --no-pager 2>&1 | head -30

echo '=== beat: последние срабатывания ==='
tail -50 /home/valstan/SETKA/logs/celery-beat.log 2>&1 | grep -iE 'sending|publish|due' | tail -15

echo '=== worker: последние завершённые задачи ==='
tail -200 /home/valstan/SETKA/logs/celery-worker.log 2>&1 | grep -iE 'succeeded|completed|published digest' | tail -15

echo '=== Redis cooldown (кто публиковал в текущем часу) ==='
redis-cli --scan --pattern 'setka:digest_last_published:*' | sort

echo '=== Beat-schedule из кода ==='
cd /home/valstan/SETKA && python -c "
from tasks.celery_app import app
for name, conf in sorted(app.conf.beat_schedule.items())[:30]:
    print(f'{name:50s} -> {conf.get(\"schedule\")}')
" 2>&1 | head -30
```

Если `$ARGUMENTS` содержит `--errors`:

```bash
echo '=== Ошибки worker за последние 24ч ==='
journalctl -u setka-celery-worker --since '24 hours ago' --no-pager 2>&1 | grep -iE 'error|critical|exception|traceback' | tail -30
```

Если `$ARGUMENTS` содержит `--region=<code>`:

```bash
echo '=== Лог worker для региона <code> за последние 6ч ==='
journalctl -u setka-celery-worker --since '6 hours ago' --no-pager 2>&1 | grep -i '<code>' | tail -40
```

## Шаг 3. Формат отчёта (на русском)

1. **Сервисы:** worker / beat — active / inactive / failed (с временем uptime).
2. **Beat последние срабатывания** — список из 5-10 последних с временем.
3. **Worker последние публикации** — таблица «время / регион / тема» из 5-10 последних.
4. **Redis cooldown (текущий час):** список регионов через запятую. Сколько ещё могут публиковать в этот час.
5. **Ошибки** (если `--errors` или они видны в `tail`):
   - количество за последние сутки;
   - топ-3 уникальных типов (по последней строке traceback или коду ошибки).
6. **Здоровье в одной строке:** ✅ всё ок / ⚠️ N ошибок в worker / ❌ beat down.

## Подсказки в отчёте

- Если `setka:digest_last_published:*` пуст — значит за текущий час никто не публиковался. Это **нормально**, если сейчас 7:00-7:30 утра до первого `novost`-запуска; **подозрительно**, если сейчас рабочее окно и таких ключей быть должно много.
- Если worker active, но публикаций за последние 6 часов нет — копнуть в `logs/celery-beat.log`: возможно beat не отправляет задачи (рассинхрон таймера).
- Если в логах `Lost connection to broker` — Redis перезапускался; обычно само лечится, но стоит подсветить.

## Что НЕ делать

- Не перезапускать сервисы без явного запроса пользователя.
- Не править beat-расписание — это в `tasks/celery_app.py`, нужен полный цикл `/reliz`.
- Не сбрасывать Redis-ключи cooldown без подтверждения — это спровоцирует мгновенную повторную публикацию.
