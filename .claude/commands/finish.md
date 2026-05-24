---
description: Мягкое закрытие сессии SETKA без деплоя — проверить PENDING_FOLLOWUPS, uncommitted, предложить commit или /reliz.
argument-hint: (без аргументов)
allowed-tools: Read, Edit, Bash, Glob, Grep, AskUserQuestion
---

# /finish — закрыть сессию разработки SETKA

Используется, когда нужно **зафиксировать сессию** (обновить документы, закоммитить рабочие правки), **но не деплоить прямо сейчас**.

Соседние команды:
- [`/reliz`](reliz.md) — для деплоя на прод (`/finish` сам не деплоит).
- [`/close_session`](close_session.md) — для записи **sticky-note** «куда мы шли» в `docs/SESSION_HANDOFF.md` (текущая нитка + следующий шаг + failed approaches). Обычный flow конца дня: сначала `/finish` для рабочих правок, потом `/close_session` для handoff'а.

## Шаг 1. Сводка изменений

```bash
git status --short --branch
git diff --stat HEAD
git log --oneline main..HEAD 2>/dev/null  # что в feature-ветке (если)
git log --oneline -5
```

Если рабочее дерево чистое и нет несмердженых коммитов — сказать «сессия и так чистая, закрывать нечего», предложить пользователю просто `/start` следующей сессии.

## Шаг 2. Подготовка к коммиту — описательный message

С 2026-05-24 хронология ведётся через git ([ADR-0001](../../docs/adr/0001-archive-dev-history.md), упразднена `docs/DEV_HISTORY.md`). Поэтому commit message должен быть **полноценным** — не однострочный заголовок:

- **Subject** (≤70 символов): Conventional Commits — `feat(scope):`, `fix(scope):`, `refactor(scope):`, `docs:`, `chore:`, `test:`.
- **Тело** (1-3 абзаца): что меняли (файлы), почему, какие тесты, как применять на проде (миграция? restart? оба? ничего?).
- В конце: `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`.

PR description (Шаг 4) расширяет это для review-контекста.

## Шаг 3. Проверка `PENDING_FOLLOWUPS.md`

1. `Read` `docs/PENDING_FOLLOWUPS.md`.
2. `AskUserQuestion` — серия (по одному):
   - «В этой сессии **закрыты** какие-то задачи из PENDING_FOLLOWUPS?» — если да, убрать строку (или пометить `~~strikethrough~~` с пометкой «закрыто в PR #N» если кратко зафиксировать).
   - «Появились **новые** техдолги или идеи?» — если да, добавить в `PENDING_FOLLOWUPS.md` с правильным приоритетом 🔴⏳🟡🟢.
   - «Есть **блокеры**, которые остаются на завтра?» — если да, поднять в раздел 🔴 в `PENDING_FOLLOWUPS.md`.

## Шаг 4. Коммит + PR (опционально)

Если в `git status` есть uncommitted-файлы:

`AskUserQuestion`: «Что делать с накопленными правками?» Опции:
- «Локальный commit (без push)» — закоммитить, ничего не пушить.
- «Commit + push + PR (без deploy)» — закоммитить, push на feature-ветку, открыть PR. Без merge и без деплоя.
- «Запусти `/reliz` — деплоим» — переключиться на `/reliz` (он сам ведёт через PR-flow + deploy).
- «Нет, оставь как есть» — закончить без коммита.

**Direct push в `main` запрещён** ([ADR-0002](../../../brain_matrica/adr/0002-pr-only-flow-no-direct-push.md)). Если сейчас на `main` — перед коммитом создать feature-ветку:

```bash
git checkout -b <type>/<slug>   # feat/, fix/, chore/, docs/, refactor/
```

Если «локальный commit»:

```bash
git add docs/PENDING_FOLLOWUPS.md <other-paths>
git commit -m "$(cat <<'EOF'
<type>(scope): <subject под 70 символов>

Что меняли, почему. Какие тесты прошли. Как применять (если что-то нужно
на проде кроме git pull + restart — описать в этом теле, не ссылаясь на
DEV_HISTORY.md, она упразднена).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

Если «commit + push + PR»:

```bash
git add docs/PENDING_FOLLOWUPS.md <other-paths>
git commit -m "$(cat <<'EOF'
<type>(scope): <subject>

<body>

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"

git push -u origin <type>/<slug>

gh pr create --title "<type>(scope): <subject>" --body "$(cat <<'EOF'
## Summary
- что меняли и почему
- какие файлы / модули задеты
- что НЕ делали (если уместно)

## Test plan
- [x] `pytest tests/ -q` — N/N зелёных
- [x] `pre-commit run --all-files` (если правка кода)
- [ ] `/check` skill после merge

## Применение на проде
- restart `setka` / `celery-worker` / `celery-beat` / ничего — что нужно
- миграции (если есть) — `database/migrations/NNN_*.sql`
EOF
)"
```

PR оставляем открытым — merge и deploy через `/reliz` или вручную после ревью.

## Шаг 5. Финальный отчёт

- Что обновили в `PENDING_FOLLOWUPS` (одна строка).
- Что закоммитили / push'нули (если делали) — commit hash + PR URL.
- Что осталось делать в следующей сессии (топ-3 пункта из 🔴⏳ + хвосты от текущей).
- Прод-состояние не трогаем — это не задача `/finish`.

## Шаг 6. Подсказка для следующей сессии

В конце:

> Прод обновлений не делал — для деплоя в следующей сессии запусти `/reliz`. Открытые задачи и контекст подгрузятся через `/start` (он смотрит `git log -20` + `gh pr list --state merged --limit 10` вместо упразднённой `DEV_HISTORY`). Если хочешь зафиксировать «куда мы шли» (для другого компа или после длительной паузы) — запусти `/close_session`, он обновит `docs/SESSION_HANDOFF.md` отдельным коммитом.

## Что НЕ делать в `/finish`

- Не push'ить на прод (`ssh setka`).
- Не перезапускать сервисы.
- Не применять миграции.
- Не закоммитить без подтверждения от пользователя (по аналогии с системным правилом).
- Не писать в `DEV_HISTORY.md` — файл упразднён (см. [ADR-0001](../../docs/adr/0001-archive-dev-history.md)). Хронология — в `git log` + PR descriptions.
