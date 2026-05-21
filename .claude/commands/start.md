---
description: Открыть новую сессию разработки SETKA — git pull, прочитать source-of-truth, опционально проба прода, отчёт о состоянии.
argument-hint: (без аргументов; `--no-prod` — пропустить SSH-probe)
allowed-tools: Read, Bash, Glob, Grep, AskUserQuestion, mcp__ccd_session__mark_chapter
---

# /start — открыть новую сессию разработки SETKA

Цель: за один заход войти в полный контекст проекта и доложить пользователю что нового, какие хвосты, чем заняться.

**Никаких изменений** — только чтение, `git fetch`, опционально `git pull --ff-only` если безопасно.

## Шаг 1. Глава сессии

Вызови `mcp__ccd_session__mark_chapter` с заголовком `СЕТКА <дата>` (используй `# currentDate` из системного контекста; формат: `СЕТКА 21 мая 2026`). В `summary` — кратко: «Открытие сессии разработки».

## Шаг 2. Source of truth (читать параллельно)

Прочитай **полностью** в одном параллельном блоке:

1. [`CLAUDE.md`](../../CLAUDE.md) — entry point, правила, lessons learned
2. [`docs/AI_DEV_GUIDE.md`](../../docs/AI_DEV_GUIDE.md) — архитектурная картина
3. [`docs/DEV_HISTORY.md`](../../docs/DEV_HISTORY.md) — что сделано в последних сессиях (читай первые ~300 строк, остальное по запросу)
4. [`docs/PENDING_FOLLOWUPS.md`](../../docs/PENDING_FOLLOWUPS.md) — открытые задачи и техдолги
5. [`docs/START_HERE.md`](../../docs/START_HERE.md) — быстрые команды на проде

Memory-файлы автоматически подгружены через `MEMORY.md` — учитывай их (особенно `reference-prod-access`, `reference-local-env`, `feedback-prod-only-ssh`, `feedback-commit-devhistory`).

## Шаг 3. Git sync (параллельно)

В одном Bash-блоке:

```bash
git status --short --branch
git fetch --all --tags --prune
git log --oneline -10
```

Затем (зависит от fetch):

```bash
git status --short --branch         # оценить ahead/behind после fetch
gh pr list --state open --limit 20 2>/dev/null | head -20  # опц.
```

**`git pull --ff-only` без подтверждения** только если: текущая ветка — `main`, есть `behind` без `ahead`, рабочее дерево чистое. Иначе — отчитаться и подождать решения. Если на worktree-ветке (`claude/...`) — pull не делать, просто доложить состояние.

## Шаг 4. Sanity-check локального окружения (параллельно)

Только чтения:

- `Glob` `venv/Scripts/python.exe` или `venv/bin/python` — есть ли venv в текущем worktree.
- Если venv есть — быстрая discovery: `.\venv\Scripts\python.exe -m pytest --co -q 2>&1 | tail -5` (или `./venv/bin/python -m pytest --co -q | tail -5` на Linux). Должно быть `159+ tests collected` без ошибок.
- `Glob` `database/migrations/*.sql` — посмотреть свежесть последней миграции (`git log -1 --format='%cs %s' -- database/migrations/`).

Если venv нет — отметить в отчёте, **не создавать сам**: подсказать пользователю команду из memory `reference-local-env`.

## Шаг 5. Прод-probe (опционально — пропускается при `--no-prod`)

Если `$ARGUMENTS` содержит `--no-prod` — пропустить шаг.
Иначе — **через `AskUserQuestion` спросить**: «Делать SSH-probe прода? (auto-mode classifier требует подтверждения)». Опции:

- «Да, проверь прод» — выполнить probe
- «Нет, пропустить» — двигаться к отчёту
- «Дай полный доступ ssh setka-prod на эту сессию» — отметить и работать дальше без вопросов

При «да» — параллельный SSH-probe (быстрый, безопасный, read-only):

```bash
ssh -o ConnectTimeout=10 setka-prod "systemctl is-active setka setka-celery-worker setka-celery-beat" 2>&1
ssh -o ConnectTimeout=10 setka-prod "curl -s -o /dev/null -w 'health: %{http_code} in %{time_total}s\n' --max-time 10 http://127.0.0.1:8000/api/health/full" 2>&1
ssh -o ConnectTimeout=10 setka-prod "cd /home/valstan/SETKA && git log --oneline -3" 2>&1
```

Если что-то не 200 / не active — отметить в отчёте, **но не диагностировать без запроса пользователя**.

## Шаг 6. Отчёт пользователю

Структура (8-14 строк, на русском):

1. **Сессия:** `СЕТКА <дата>` — отмечена.
2. **Что нового** (последняя запись из `DEV_HISTORY.md`): 1-2 строки.
3. **Git:** ветка, ahead/behind, был ли `pull`, uncommitted-файлы (если есть).
4. **Локально:** venv (есть/нет), `pytest --co` (N tests / ошибки).
5. **Прод** (если делали probe): systemd (active/inactive), `/api/health/full` (200/ошибка), последний коммит на проде.
6. **🔴 Блокеры и ⏳ в процессе** из `PENDING_FOLLOWUPS.md`.
7. **Самые свежие 🟡 техдолги** (топ-3) и 🟢 идеи (топ-3) — кратко.
8. **Чем займёмся?** — открытый вопрос.

Если есть блокеры — подсветить отдельно. Если всё чисто — так и сказать.

## Шаг 7. Напоминание для закрытия сессии

В конце ответа сноска:

> При значимых правках — обнови `docs/DEV_HISTORY.md` (новый блок сверху, шаблон в шапке файла) и [`PENDING_FOLLOWUPS.md`](../../docs/PENDING_FOLLOWUPS.md) **до коммита**. Команда `/reliz` ведёт через релиз; `/finish` — через закрытие сессии без деплоя.
