---
description: Просмотр прод-логов SETKA через SSH с фильтрами.
argument-hint: <service> [--grep PATTERN] [--since "1 hour ago"] [-n N]
allowed-tools: Bash, AskUserQuestion
---

# /logs — просмотр прод-логов

`$ARGUMENTS` — позиционно: первый аргумент = имя сервиса/лог-файла. Дальше — флаги.

## Сервисы

| `<service>` | Где |
|---|---|
| `app` / `setka` | `/home/valstan/SETKA/logs/app.log` или `journalctl -u setka` |
| `worker` | `/home/valstan/SETKA/logs/celery-worker.log` или `journalctl -u setka-celery-worker` |
| `beat` | `/home/valstan/SETKA/logs/celery-beat.log` или `journalctl -u setka-celery-beat` |
| `nginx` | `/home/valstan/SETKA/logs/nginx_access.log` + `nginx_error.log` |
| `backup` | `/home/valstan/SETKA/logs/backup.log` |

По умолчанию — файл из `logs/` (там запись приложения, более удобная). Через `--journal` — переключиться на `journalctl` (там systemd-уровень: рестарты, сигналы).

## Флаги

- `--grep PATTERN` — фильтрация (case-insensitive `grep -iE`)
- `--since "1 hour ago"` — только новее этого времени (для `journalctl`) или эквивалент `awk` по timestamp для файлов
- `-n N` — последние N строк (по умолчанию 100)
- `--errors` — шорткат для `--grep 'error|critical|exception|traceback'`
- `--journal` — читать из `journalctl -u <service>` вместо файла

## Шаг 1. Подтверждение прод-доступа

**Через `AskUserQuestion`** (если ещё не давали в этом чате).

## Шаг 2. Сборка команды

Парсинг `$ARGUMENTS`:

```python
# pseudo
service = args[0]  # обязательный
n = int(arg('-n', 100))
since = arg('--since')
grep_pattern = arg('--grep') or ('error|critical|exception|traceback' if '--errors' in args else None)
use_journal = '--journal' in args
```

Маппинг `service → путь/unit`:

```python
mapping = {
    'app': ('/home/valstan/SETKA/logs/app.log', 'setka'),
    'setka': ('/home/valstan/SETKA/logs/app.log', 'setka'),
    'worker': ('/home/valstan/SETKA/logs/celery-worker.log', 'setka-celery-worker'),
    'beat': ('/home/valstan/SETKA/logs/celery-beat.log', 'setka-celery-beat'),
    'nginx': ('/home/valstan/SETKA/logs/nginx_access.log', None),
    'backup': ('/home/valstan/SETKA/logs/backup.log', None),
}
```

## Шаг 3. Выполнение

**Из файла:**

```bash
ssh -o ConnectTimeout=10 setka "tail -n <N>x4 <path> | <grep_filter_if_any> | tail -n <N>" 2>&1
```

(берём с запасом 4x чтобы grep'у было из чего фильтровать).

**Из journalctl (если `--journal`):**

```bash
ssh -o ConnectTimeout=10 setka "journalctl -u <unit> --since '<since>' --no-pager -n <N> <grep_via_pipe>" 2>&1
```

## Шаг 4. Отчёт

- Заголовок: «Логи `<service>` за последние ~N строк / since `<since>`».
- Если `--grep` — показать паттерн.
- Сами строки в `code block`.
- Если ничего не нашлось — `(пусто)`.
- В конце 1-2 строки наблюдений: «Видны N ошибок типа X», «Логи чистые», «Worker рестартовал в HH:MM».

## Примеры использования

- `/logs worker` — последние 100 строк worker'а
- `/logs worker --errors -n 200` — последние 200 строк, только ошибки
- `/logs beat --since "30 minutes ago"` — что beat делал последние 30 минут
- `/logs app --journal --since "1 hour ago"` — systemd-уровень за час (рестарты)
- `/logs worker --grep "malmyzh|novost"` — только про регион/тему

## Что НЕ делать

- Не качать всё на локальную машину (`scp` весь app.log) — он большой и не нужен.
- Не предлагать `> /dev/null` или `truncate` на прод-лог без явного запроса.
- Логи приложения могут содержать VK-токены в URL — **не показывать** строки с `access_token=` пользователю в чате; маскировать как `access_token=***`.
