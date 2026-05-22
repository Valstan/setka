---
description: Мягкое закрытие сессии SETKA без деплоя — проверить DEV_HISTORY/PENDING_FOLLOWUPS, uncommitted, предложить commit или /reliz.
argument-hint: (без аргументов)
allowed-tools: Read, Edit, Bash, Glob, Grep, AskUserQuestion
---

# /finish — закрыть сессию разработки SETKA

Используется, когда нужно **зафиксировать сессию** (обновить документы, закоммитить), **но не деплоить прямо сейчас**. Для деплоя — `/reliz`.

## Шаг 1. Сводка изменений

```bash
git status --short --branch
git diff --stat HEAD
git log --oneline main..HEAD 2>/dev/null  # что в feature-ветке (если)
git log --oneline -5
```

Если рабочее дерево чистое и нет несмердженых коммитов — сказать «сессия и так чистая, закрывать нечего», предложить пользователю просто `/start` следующей сессии.

## Шаг 2. Проверка `DEV_HISTORY.md`

1. `Read` `docs/DEV_HISTORY.md` (первые 60 строк).
2. Есть ли блок за сегодняшнюю дату (`# currentDate` из системного контекста)?
3. Если нет — сравнить с тем, что было в `git diff`:
   - Если изменения тривиальные (только doc / комментарий / переименование переменной) — спросить `AskUserQuestion`: «Добавлять запись в `DEV_HISTORY.md`?» с опциями «Да / Нет, изменения тривиальные».
   - Если изменения значимые (новый функционал, изменение пайплайна, фильтра, публикации, миграция) — **обязательно** предложить написать новую запись. Сгенерировать её на основе `git diff` и показать пользователю на правку через `Edit`-предложение.

Шаблон записи — в шапке `DEV_HISTORY.md`.

## Шаг 3. Проверка `PENDING_FOLLOWUPS.md`

1. `Read` `docs/PENDING_FOLLOWUPS.md`.
2. `AskUserQuestion` — серия (по одному):
   - «В этой сессии **закрыты** какие-то задачи из PENDING_FOLLOWUPS?» — если да, перенести в `DEV_HISTORY.md` и убрать из `PENDING_FOLLOWUPS.md`.
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
git add docs/DEV_HISTORY.md docs/PENDING_FOLLOWUPS.md <other-paths>
git commit -m "<conventional message>

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

Если «commit + push + PR»:

```bash
git add docs/DEV_HISTORY.md docs/PENDING_FOLLOWUPS.md <other-paths>
git commit -m "<conventional message>

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"

git push -u origin <type>/<slug>

gh pr create --title "..." --body "$(cat <<'EOF'
## Summary
- ...

## Test plan
- [ ] pytest зелёный локально
EOF
)"
```

PR оставляем открытым — merge и deploy через `/reliz` или вручную после ревью.

## Шаг 5. Финальный отчёт

- Что обновили в документах (одна строка про DEV_HISTORY, одна про PENDING).
- Что закоммитили / push'нули (если делали).
- Что осталось делать в следующей сессии (топ-3 пункта из 🔴⏳ + хвосты от текущей).
- Прод-состояние не трогаем — это не задача `/finish`.

## Шаг 6. Подсказка для следующей сессии

В конце:

> Прод обновлений не делал — для деплоя в следующей сессии запусти `/reliz`. Открытые задачи и контекст подгрузятся через `/start`.

## Что НЕ делать в `/finish`

- Не push'ить на прод (`ssh setka-prod`).
- Не перезапускать сервисы.
- Не применять миграции.
- Не закоммитить без подтверждения от пользователя (по аналогии с системным правилом).
