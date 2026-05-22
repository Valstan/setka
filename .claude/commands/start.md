---
description: Открыть новую сессию разработки SETKA — mailbox-проверка brain_matrica, git pull, прочитать source-of-truth, опционально проба прода, отчёт о состоянии.
argument-hint: (без аргументов; `--no-prod` — пропустить SSH-probe; `--no-mailbox` — пропустить проверку brain mailbox)
allowed-tools: Read, Write, Bash, Glob, Grep, AskUserQuestion, mcp__ccd_session__mark_chapter
---

# /start — открыть новую сессию разработки SETKA

Цель: за один заход войти в полный контекст проекта и доложить пользователю что нового, какие хвосты, чем заняться.

**Никаких изменений в коде** — только чтение, `git fetch`, опционально `git pull --ff-only` если безопасно. Запись разрешена только в `../brain_matrica/mailboxes/setka/.last-seen` (см. Шаг 0).

## Шаг 0. Mailbox check (brain_matrica)

setka управляется meta-репо [brain_matrica](../../../brain_matrica/) через систему почтовых ящиков ([ADR-0001](../../../brain_matrica/adr/0001-brain-projects-mailboxes.md)). Проверка делается **до** SoT-чтения.

Если `$ARGUMENTS` содержит `--no-mailbox` — пропустить.

1. **Найти WORKSPACE_ROOT.** brain_matrica лежит рядом — путь `../brain_matrica/` от корня setka. Если нет — отметить в отчёте «brain_matrica не найдена, mailbox-проверка пропущена», далее не выполнять Шаг 0.
2. **Сканировать** `../brain_matrica/mailboxes/setka/from-brain/*.md` (только корень, **не** `DRAFTS/`, **не** `ARCHIVE/`).
3. Для каждого письма прочитать frontmatter: `kind`, `urgency`, `compliance`, `topic`.
4. **Retroactive-правило** для писем без `compliance` ([ADR-0001 v2](../../../brain_matrica/adr/0001-brain-projects-mailboxes.md#compliance-levels)):
   - `kind: directive` без `compliance` → читать как `mandate`
   - `kind: idea` без `compliance` → читать как `recommend`
5. **Доложить пользователю** в формате `[urgency COMPLIANCE]` (compliance в верхнем регистре, через пробел) **до** обычного onboarding-workflow:
   ```
   📬 N писем от brain_matrica:
   - [high MANDATE] 2026-05-22-pr-only-flow-directive.md — PR-only flow
   - [normal SHOULD] 2026-05-NN-...md — topic
   - [low MAY] 2026-05-NN-...md — topic
   ```
   Compliance-mapping: `mandate=MANDATE`, `recommend=SHOULD`, `suggest=MAY`.
   Любое `urgency: high` или `compliance: mandate` упомянуть отдельно даже если письмо одно.
6. **Записать** ISO-8601 timestamp (UTC) в `../brain_matrica/mailboxes/setka/.last-seen` (overwrite, одна строка). Это маркер последнего захода — brain использует его в reflection «X не заходил Y дней».
7. **Реакция на письма** определяется compliance ([ADR-0001 §Compliance levels](../../../brain_matrica/adr/0001-brain-projects-mailboxes.md#compliance-levels)):
   - `mandate` — применить обязательно; невозможно технически → `to-brain/` с `kind=feedback`, `urgency=high`, конкретный блокер.
   - `recommend` — применить с возможной адаптацией; не подходит → `to-brain/` с обоснованием отказа (`kind=feedback`).
   - `suggest` — по усмотрению; применил — feedback приветствуется, но не обязателен.
8. **Не обрабатывать письма автоматически в /start** — только доклад. Обработка — после OK пользователя; затем письмо двигается в `from-brain/ARCHIVE/` с дописанной секцией `## Result` и acknowledgement-письмом в `to-brain/`. Все изменения в `brain_matrica` идут через PR (см. [ADR-0002](../../../brain_matrica/adr/0002-pr-only-flow-no-direct-push.md)).

**Что НЕЛЬЗЯ:**
- Редактировать любые файлы `brain_matrica/` кроме `mailboxes/setka/` (включая `.last-seen` — это часть своего mailbox'а).
- Писать напрямую в чужие mailboxes (`mailboxes/GONBA/`, `mailboxes/MatricaRMZ/` и пр.) — только через brain.
- Удалять файлы из `from-brain/ARCHIVE/`.

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

0. **📬 Mailbox:** `N писем от brain_matrica` со списком `[urgency COMPLIANCE] slug — topic` (из Шага 0). Любые `MANDATE` / `high` выделить отдельно. Если писем нет — `📬 mailbox чист`.
1. **Сессия:** `СЕТКА <дата>` — отмечена.
2. **Что нового** (последняя запись из `DEV_HISTORY.md`): 1-2 строки.
3. **Git:** ветка, ahead/behind, был ли `pull`, uncommitted-файлы (если есть).
4. **Локально:** venv (есть/нет), `pytest --co` (N tests / ошибки).
5. **Прод** (если делали probe): systemd (active/inactive), `/api/health/full` (200/ошибка), последний коммит на проде.
6. **🔴 Блокеры и ⏳ в процессе** из `PENDING_FOLLOWUPS.md`.
7. **Самые свежие 🟡 техдолги** (топ-3) и 🟢 идеи (топ-3) — кратко.
8. **Чем займёмся?** — открытый вопрос. Если есть `MANDATE`-письма в mailbox — предложить их первыми.

Если есть блокеры или `MANDATE`-почта — подсветить отдельно. Если всё чисто — так и сказать.

## Шаг 7. Напоминание для закрытия сессии

В конце ответа сноска:

> При значимых правках — обнови `docs/DEV_HISTORY.md` (новый блок сверху, шаблон в шапке файла) и [`PENDING_FOLLOWUPS.md`](../../docs/PENDING_FOLLOWUPS.md) **до коммита**. Команда `/reliz` ведёт через релиз; `/finish` — через закрытие сессии без деплоя.
