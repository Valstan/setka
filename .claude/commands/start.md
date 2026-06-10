---
description: Открыть новую сессию разработки SETKA — mailbox-проверка brain_matrica (read-only), git pull, прочитать source-of-truth, опционально проба прода, отчёт о состоянии.
argument-hint: (без аргументов; `--no-prod` — пропустить SSH-probe; `--no-mailbox` — пропустить проверку brain mailbox)
allowed-tools: Read, Write, Bash, Glob, Grep, AskUserQuestion, mcp__ccd_session__mark_chapter
---

# /start — открыть новую сессию разработки SETKA

Цель: за один заход войти в полный контекст проекта и доложить пользователю что нового, какие хвосты, чем заняться.

**Никаких изменений в чужих репо** — `brain_matrica` трогается **только на чтение** (`git pull --ff-only`). Запись разрешена только в свой репо `setka/mailbox/to-brain/` и обычные файлы проекта.

## Шаг 0. Mailbox check (brain_matrica — read-only)

setka управляется meta-репо [brain_matrica](../../../brain_matrica/) через асимметричный mailbox-протокол ([ADR-0001](../../../brain_matrica/adr/0001-brain-projects-mailboxes.md) v3 от 2026-05-23): каждая сторона пишет **только в свой репо**. Проверка делается **до** SoT-чтения.

Если `$ARGUMENTS` содержит `--no-mailbox` — пропустить.

### 0.1. Подтянуть brain_matrica (read-only)

```bash
cd ../brain_matrica && git pull --ff-only origin main 2>&1 | tail -3
```

Если рабочее дерево brain_matrica не чистое или нет fast-forward — отметить в отчёте «brain_matrica conflicts, mailbox skipped», далее не выполнять Шаг 0. **Никаких force-операций.**

Если папки `../brain_matrica/` нет — отметить «brain_matrica не найдена, mailbox-проверка пропущена», далее не выполнять Шаг 0.

### 0.2. Сканировать входящие

**⚠️ НЕ использовать `Glob`** — он не видит пути вне корня проекта setka и возвращает «No files found» даже когда письма есть (инцидент 2026-05-24). Использовать `Bash`:

```bash
ls ../brain_matrica/mailboxes/setka/from-brain/*.md 2>/dev/null
```

Только корень (`*.md` без рекурсии), **не** `DRAFTS/`, **не** `ARCHIVE/`. Для каждого письма прочитать через `Read` (по конкретному пути работает) и извлечь frontmatter: `kind`, `urgency`, `compliance`, `topic`.

### 0.3. Retroactive-правило

Для писем без `compliance` ([ADR-0001 v2 §Compliance levels](../../../brain_matrica/adr/0001-brain-projects-mailboxes.md#compliance-levels)):
- `kind: directive` без `compliance` → читать как `mandate`
- `kind: idea` без `compliance` → читать как `recommend`

### 0.4. Доложить пользователю

В формате `[urgency COMPLIANCE]` (compliance в верхнем регистре, через пробел) **до** обычного onboarding-workflow:

```
📬 N писем от brain_matrica:
- [high MANDATE] 2026-05-23-slug.md — short topic
- [normal SHOULD] 2026-05-NN-...md — topic
- [low MAY] 2026-05-NN-...md — topic
```

Compliance-mapping: `mandate=MANDATE`, `recommend=SHOULD`, `suggest=MAY`. Любое `urgency: high` или `compliance: mandate` упомянуть отдельно даже если письмо одно.

### 0.5. Реакция на письма

Определяется compliance ([ADR-0001 §Compliance levels](../../../brain_matrica/adr/0001-brain-projects-mailboxes.md#compliance-levels)):

| compliance | Реакция |
|---|---|
| `mandate` (MUST) | Применить обязательно. Невозможно технически → ответить в `setka/mailbox/to-brain/` с `kind=feedback`, `urgency=high`, конкретный блокер. |
| `recommend` (SHOULD) | Применить с адаптацией. Не подходит → `setka/mailbox/to-brain/` с обоснованием отказа (`kind=feedback`). Молчать нельзя. |
| `suggest` (MAY) | По усмотрению. Применил — feedback приветствуется, но не обязателен. |

### 0.6. Если нужно ответить brain'у

Файл идёт в **свой репо**: `setka/mailbox/to-brain/YYYY-MM-DD-slug.md` (создать через `Write`). Коммит — в setka репо отдельным PR или вместе с тематической работой ([ADR-0002](../../../brain_matrica/adr/0002-pr-only-flow-no-direct-push.md), PR-only flow).

Шаблон frontmatter:

```yaml
---
from: setka
to: brain
date: YYYY-MM-DD
topic: ...
kind: feedback | report | question | idea
compliance: suggest    # required для kind=idea
urgency: low | normal | high
ref:
  - <название исходного письма>.md   # опционально, если отвечаем
---
```

### 0.7. Не обрабатывать письма автоматически в /start

Только доклад. Обработка — после OK пользователя. Архивация исходящих писем у setka **не делается** ([asymmetry-fix](../../../brain_matrica/mailboxes/setka/from-brain/2026-05-23-mailbox-asymmetry-fix.md) §Архивация — MVP).

### Что НЕЛЬЗЯ

- ❌ **Писать в `../brain_matrica/`** — никаких `Write`/`Edit`/`git add`/`git commit` в этот репо. Доступ только `git pull --ff-only origin main`.
- ❌ **Писать в `../brain_matrica/mailboxes/setka/to-brain/`** или `.last-seen` — устаревший канал, не используется.
- ❌ **Архивировать письма** в `../brain_matrica/mailboxes/setka/from-brain/ARCHIVE/` из проектной сессии — это зона brain'а.
- ❌ **Писать в чужие mailbox'ы** (`mailboxes/GONBA/`, `mailboxes/MatricaRMZ/` и пр.) — не моя зона ни в каком виде.

## Шаг 1. Глава сессии

Вызови `mcp__ccd_session__mark_chapter` с заголовком `СЕТКА <дата>` (используй `# currentDate` из системного контекста; формат: `СЕТКА 21 мая 2026`). В `summary` — кратко: «Открытие сессии разработки».

## Шаг 2. Git sync — ДО чтения SESSION_HANDOFF (pool #032)

**Порядок жёсткий** ([pool #032](../../../brain_matrica/cross-project-ideas/ideas/032-session-start-sync-before-state.md), директива brain 2026-06-09): сначала синхронизация с `origin`, **только потом** чтение `SESSION_HANDOFF` / `PENDING`. Пользователь работает на разных машинах — другая машина могла запушить свежий handoff; чтение до pull = работа по устаревшему состоянию (что-то уже сделано, новые задачи прозёваны).

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

**`git pull --ff-only` без подтверждения** только если: текущая ветка — `main`, есть `behind` без `ahead`, рабочее дерево чистое. Иначе — отчитаться и подождать решения (SoT-файлы в этом случае читать можно, но в отчёте пометить «handoff может быть не последним — origin впереди»). Если на worktree-ветке (`claude/...`) — pull не делать, просто доложить состояние.

## Шаг 3. Source of truth (читать параллельно, ПОСЛЕ Шага 2)

Прочитай **полностью** в одном параллельном блоке:

1. [`docs/SESSION_HANDOFF.md`](../../docs/SESSION_HANDOFF.md) — sticky-note с прошлой сессии: `Status`, текущая нитка, следующий шаг, failed approaches. **Если файла нет** или `Status: IDLE` — нет активной нитки, идём по обычному onboarding. Сверь `Updated:` с датой последнего коммита — устаревшему handoff (старше последних merged PR) не доверять слепо, ground truth — `git log`.
2. [`CLAUDE.md`](../../CLAUDE.md) — entry point, правила, lessons learned
3. [`docs/AI_DEV_GUIDE.md`](../../docs/AI_DEV_GUIDE.md) — архитектурная картина
4. `git log --oneline -20` + `gh pr list --state merged --limit 10` — что сделано в последних сессиях (заменяет старый `DEV_HISTORY.md`, см. [ADR-0001](../../docs/adr/0001-archive-dev-history.md)). Для конкретного PR — `gh pr view <N>`.
5. [`docs/PENDING_FOLLOWUPS.md`](../../docs/PENDING_FOLLOWUPS.md) — открытые задачи и техдолги
6. [`docs/START_HERE.md`](../../docs/START_HERE.md) — быстрые команды на проде
7. [`docs/adr/`](../../docs/adr/) — посмотри список ADR-ов (заголовков достаточно для оценки контекста; читай файлом при необходимости)

Memory-файлы автоматически подгружены через `MEMORY.md` — учитывай их (особенно `reference-ssh-alias`, `remote-access-ssh-only`, `workflow-dev-history` — последний теперь говорит «DEV_HISTORY упразднена, пиши описательные commit messages»).

### 3.1. Самопроверка старения PENDING (pool #033)

При чтении `PENDING_FOLLOWUPS.md` отдельно выцепить **протухшие** открытые пункты ([pool #033](../../../brain_matrica/cross-project-ideas/ideas/033-deferred-backlog-aging-retriage.md)): тег `stale`, либо открыто > 30 дней, либо `snooze ≥ 3` (конвенция меток — в шапке самого файла). Найденное вынести в отчёт (Шаг 6) с предложением **ре-триажа тремя исходами**: возобновить / переформулировать под текущий код / выкинуть (с причиной). Не возобновлять слепо. Пункты `parked` (сознательно отложены до явного условия) не всплывать, пока условие не наступило.

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
- «Дай полный доступ ssh setka на эту сессию» — отметить и работать дальше без вопросов

При «да» — параллельный SSH-probe (быстрый, безопасный, read-only):

```bash
ssh -o ConnectTimeout=10 setka "systemctl is-active setka setka-celery-worker setka-celery-beat" 2>&1
ssh -o ConnectTimeout=10 setka "curl -s -o /dev/null -w 'health: %{http_code} in %{time_total}s\n' --max-time 10 http://127.0.0.1:8000/api/health/full" 2>&1
ssh -o ConnectTimeout=10 setka "cd /home/valstan/SETKA && git log --oneline -3" 2>&1
```

Если что-то не 200 / не active — отметить в отчёте, **но не диагностировать без запроса пользователя**.

## Шаг 6. Отчёт пользователю

Структура (8-14 строк, на русском):

0. **📬 Mailbox:** `N писем от brain_matrica` со списком `[urgency COMPLIANCE] slug — topic` (из Шага 0). Любые `MANDATE` / `high` выделить отдельно. Если писем нет — `📬 mailbox чист`.
1. **Сессия:** `СЕТКА <дата>` — отмечена.
2. **Нитка из `SESSION_HANDOFF.md`**: если `Status: ACTIVE` — текущая нитка + следующий шаг дословно. Если `Status: IDLE` или файла нет — «Активной нитки нет, открытая стартовая позиция».
3. **Что нового** (заголовки последних 3-5 merged PR-ов или коммитов на main): 1-2 строки.
4. **Git:** ветка, ahead/behind, был ли `pull`, uncommitted-файлы (если есть).
5. **Локально:** venv (есть/нет), `pytest --co` (N tests / ошибки).
6. **Прод** (если делали probe): systemd (active/inactive), `/api/health/full` (200/ошибка), последний коммит на проде.
7. **🔴 Блокеры и ⏳ в процессе** из `PENDING_FOLLOWUPS.md`.
7.5. **⏱ Протухшее** (из Шага 3.1, если есть): пункты `stale` / >30 дней / snooze≥3 — с предложением ре-триажа (возобновить / переформулировать / выкинуть).
8. **Самые свежие 🟡 техдолги** (топ-3) и 🟢 идеи (топ-3) — кратко.
9. **Чем займёмся?** — открытый вопрос. Приоритет: `MANDATE`-письма → активная нитка из handoff → 🔴 блокеры → выбор пользователя.

Если есть блокеры, `MANDATE`-почта или активная нитка с конкретным «следующим шагом» — подсветить отдельно. Если всё чисто — так и сказать.

## Шаг 7. Напоминание для закрытия сессии

В конце ответа сноска:

> При значимых правках — описательный commit-message (Conventional Commits) + PR description заменяют старую `DEV_HISTORY.md` (см. [ADR-0001](../../docs/adr/0001-archive-dev-history.md)). Открытые/новые техдолги — в [`PENDING_FOLLOWUPS.md`](../../docs/PENDING_FOLLOWUPS.md) **до коммита**. `/reliz` ведёт через релиз с деплоем; [`/close_session`](close_session.md) — **единственная команда закрытия сессии**: коммитит+пушит ВСЁ на GitHub (источник истины между машинами), фиксирует [`docs/SESSION_HANDOFF.md`](../../docs/SESSION_HANDOFF.md) и проверяет sync-гейт. Запускается и фразой «закрой сессию [разработки]».
