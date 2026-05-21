---
description: Health-check одной кнопкой — pytest локально + prod systemd + curl health + Celery последние публикации.
argument-hint: [--quick — без pytest, --no-prod — только локально]
allowed-tools: Bash, Glob, Grep, AskUserQuestion
---

# /check — быстрая диагностика SETKA

Запускает параллельные проверки и собирает короткий отчёт-таблицу.

## Шаг 1. Локальные проверки (параллельно)

```bash
echo '=== git ==='
git status --short --branch
git fetch --all --prune 2>&1 | tail -1
git log --oneline -3

echo '=== local venv ==='
ls venv/Scripts/python.exe 2>/dev/null && echo 'venv: ok (windows)' || ls venv/bin/python 2>/dev/null && echo 'venv: ok (linux)' || echo 'venv: MISSING'
```

Если `$ARGUMENTS != *--quick*` И venv есть — прогнать тесты:

```bash
# Windows
.\venv\Scripts\python.exe -m pytest tests/ -q 2>&1 | tail -10
# или Linux
./venv/bin/python -m pytest tests/ -q 2>&1 | tail -10
```

## Шаг 2. Прод-probe (если не `--no-prod`)

**Через `AskUserQuestion`** подтвердить SSH-доступ (если пользователь ещё не дал «полный доступ» в этом чате). Опции: «Делай», «Нет», «Полный доступ на сессию».

Параллельно по SSH:

```bash
ssh -o ConnectTimeout=10 setka-prod "systemctl is-active setka setka-celery-worker setka-celery-beat" 2>&1

ssh -o ConnectTimeout=10 setka-prod "curl -s -o /dev/null -w '/api/health/full: %{http_code} (%{time_total}s)\n' --max-time 15 http://127.0.0.1:8000/api/health/full" 2>&1

ssh -o ConnectTimeout=10 setka-prod "cd /home/valstan/SETKA && git log --oneline -3 && git status --short" 2>&1

# Сколько регионов опубликовалось в этот час
ssh -o ConnectTimeout=10 setka-prod "redis-cli --scan --pattern 'setka:digest_last_published:*' | wc -l" 2>&1

# Ошибки в worker за последний час
ssh -o ConnectTimeout=10 setka-prod "journalctl -u setka-celery-worker --since '1 hour ago' --no-pager 2>&1 | grep -iE 'error|critical|exception' | tail -5" 2>&1
```

## Шаг 3. Формат отчёта (таблица)

| Что | Статус |
|---|---|
| локально / git | clean / N ahead / M behind |
| локально / venv | ✅ есть / ❌ нет |
| локально / pytest | ✅ 159/159 / ❌ N failed |
| прод / setka.service | ✅ active / ❌ inactive |
| прод / setka-celery-worker | ✅ active / ❌ ... |
| прод / setka-celery-beat | ✅ active / ❌ ... |
| прод / /api/health/full | ✅ 200 / ❌ ... |
| прод / git HEAD | <hash> <subject> |
| Celery / cooldown ключей | N регионов опубликовали в текущем часу |
| worker / ошибок за час | 0 / N (показать последние) |

В конце — короткое summary: «всё ок» или «проблема в X, посмотри».

Если pytest упал — показать **последние ~10 строк** вывода (имя теста, traceback-haed). Не диагностировать без запроса.
