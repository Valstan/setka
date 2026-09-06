---
description: Открыть новую сессию разработки SETKA — mailbox-проверка brain_matrica (два канала: локально + GitHub API, без sync), git pull своего репо, прочитать source-of-truth, опционально проба прода, отчёт о состоянии.
argument-hint: (без аргументов; `--no-prod` — пропустить SSH-probe; `--no-mailbox` — пропустить проверку brain mailbox)
allowed-tools: Read, Write, Bash, Glob, Grep, AskUserQuestion, mcp__ccd_session__mark_chapter
---

# /start — открыть новую сессию разработки SETKA

Цель: за один заход войти в полный контекст проекта и доложить пользователю что нового, какие хвосты, чем заняться.

**Никаких изменений в чужих репо** — `brain_matrica` и любой sibling-репо читаются **только на чтение, без синхронизирующих команд** (`git pull`/`fetch`/`checkout` запрещены, canon 2026-08-04). Входящий mailbox читается из двух каналов: локально + GitHub API. Запись разрешена только в свой репо `setka/mailbox/to-brain/` и обычные файлы проекта.

## Шаг 0. Mailbox check (brain_matrica — read-only)

setka управляется meta-репо [brain_matrica](../../../brain_matrica/) через асимметричный mailbox-протокол ([ADR-0001](../../../brain_matrica/adr/0001-brain-projects-mailboxes.md) v3 от 2026-05-23): каждая сторона пишет **только в свой репо**. Проверка делается **до** SoT-чтения. Синхронизировать `brain_matrica` **нельзя** (canon 2026-08-04) — только чтение локальной копии + GitHub API.

Если `$ARGUMENTS` содержит `--no-mailbox` — пропустить.

### 0.1. Сканировать входящие — два канала (без pull/fetch/clone)

**Канал A — локально.** Только корень (`*.md` без рекурсии), **не** `DRAFTS/`, **не** `ARCHIVE/`. Для каждого письма прочитать через `Read` (по конкретному пути работает) и извлечь frontmatter: `kind`, `urgency`, `compliance`, `topic`.

Список — через `Bash` (каталог лежит вне корня проекта):

```bash
ls ../brain_matrica/mailboxes/setka/from-brain/*.md 2>/dev/null
```

**Канал B — GitHub API** (`main` того же репо, без clone/fetch/pull). Запрос на каждый запуск:

```bash
gh api repos/Valstan/brain_matrica/contents/mailboxes/setka/from-brain --jq '.[].name'
```

`gh api` работает и без токена на публичном репо; при 404/лимите — `gh auth status`.

**Сведение каналов по каждому письму.** Набор = объединение. Одноимённое письмо различается → свежесть по истории **именно этого пути**: незакоммиченная локальная версия — свежее; иначе последний локальный коммит файла vs последний коммит пути на GitHub; порядок не определяется — прочитать обе версии, явно отметить конфликт, **не перезаписывать**. Свежесть одного письма/проекта не переносится на другие.

### 0.2. Retroactive-правило

Для писем без `compliance` ([ADR-0001 v2 §Compliance levels](../../../brain_matrica/adr/0001-brain-projects-mailboxes.md#compliance-levels)):
- `kind: directive` без `compliance` → читать как `mandate`
- `kind: idea` без `compliance` → читать как `recommend`

### 0.3. Доложить пользователю

В формате `[urgency COMPLIANCE]` (compliance в верхнем регистре, через пробел) **до** обычного onboarding-workflow:

```
📬 N писем от brain_matrica:
- [high MANDATE] 2026-05-23-slug.md — short topic
- [normal SHOULD] 2026-05-NN-...md — topic
- [low MAY] 2026-05-NN-...md — topic
```

Compliance-mapping: `mandate=MANDATE`, `recommend=SHOULD`, `suggest=MAY`. Любое `urgency: high` или `compliance: mandate` упомянуть отдельно даже если письмо одно.

### 0.4. Реакция на письма

Определяется compliance ([ADR-0001 §Compliance levels](../../../brain_matrica/adr/0001-brain-projects-mailboxes.md#compliance-levels)):

| compliance | Реакция |
|---|---|
| `mandate` (MUST) | Применить обязательно. Невозможно технически → ответить в `setka/mailbox/to-brain/` с `kind=feedback`, `urgency=high`, конкретный блокер. |
| `recommend` (SHOULD) | Применить с адаптацией. Не подходит → `setka/mailbox/to-brain/` с обоснованием отказа (`kind=feedback`). Молчать нельзя. |
| `suggest` (MAY) | По усмотрению. Применил — feedback приветствуется, но не обязателен. |

### 0.5. Если нужно ответить brain'у

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

### 0.6. Не обрабатывать письма автоматически в /start

Только доклад. Обработка — после OK пользователя. Архивация исходящих писем у setka **не делается** ([asymmetry-fix](../../../brain_matrica/mailboxes/setka/from-brain/2026-05-23-mailbox-asymmetry-fix.md) §Архивация — MVP).

### Что НЕЛЬЗЯ

- ❌ **Синхронизировать `../brain_matrica/`** — никаких `git pull`/`fetch`/`checkout`/`reset` (canon 2026-08-04); только чтение локальной копии + GitHub API.
- ❌ **Писать в `../brain_matrica/`** — никаких `Write`/`Edit`/`git add`/`git commit` в этот репо.
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

0. **Машинное состояние прошлой сессии** (пишут хуки, без участия модели):
   - `.claude/session-state/latest.md` — полная выжимка на ЭТОЙ машине. SessionStart печатает
     её сам, когда она свежее handoff'а, то есть прошлую сессию закрыли молча;
   - `git show origin/session-state:SESSION_STATE.md` — то же состояние с ЛЮБОЙ машины владельца
     (без дословных реплик), а `git log --oneline origin/wip/<машина>` — снимок незакоммиченной
     работы, оставшейся на другом компьютере. Смотреть, если handoff протух, а на origin есть
     свежие `session-state`/`wip/*` (после `git fetch` Шага 2).

   Если handoff свежее машинного состояния — правда о нитке в нём, снимки игнорируем.
1. [`docs/SESSION_HANDOFF.md`](../../docs/SESSION_HANDOFF.md) — sticky-note с прошлой сессии: `Status`, текущая нитка, следующий шаг, failed approaches. **Если файла нет** или `Status: IDLE` — нет активной нитки, идём по обычному onboarding. Сверь `Updated:` с датой последнего коммита — устаревшему handoff (старше последних merged PR) не доверять слепо, ground truth — `git log`.
2. [`AGENTS.md`](../../AGENTS.md) — **канон проектных правил** (границы, git-flow, mailbox, ярусы автономии, чего не делать). `CLAUDE.md` — тонкий адаптер к нему с нюансами Claude Code, читать после и только его.
3. [`docs/AI_DEV_GUIDE.md`](../../docs/AI_DEV_GUIDE.md) — архитектурная картина
4. `git log --oneline -20` + `gh pr list --state merged --limit 10` — что сделано в последних сессиях (заменяет старый `DEV_HISTORY.md`, см. [ADR-0001](../../docs/adr/0001-archive-dev-history.md)). Для конкретного PR — `gh pr view <N>`.
5. [`docs/PENDING_FOLLOWUPS.md`](../../docs/PENDING_FOLLOWUPS.md) — открытые задачи и техдолги
6. [`docs/START_HERE.md`](../../docs/START_HERE.md) — быстрые команды на проде
7. [`docs/adr/`](../../docs/adr/) — посмотри список ADR-ов (заголовков достаточно для оценки контекста; читай файлом при необходимости)

Прод-доступ и правила — по [`docs/REMOTE_ACCESS.md`](../../docs/REMOTE_ACCESS.md) и `AGENTS.md`; описательные commit messages вместо истории — [ADR-0001](../../docs/adr/0001-archive-dev-history.md).

### 3.1. Самопроверка старения PENDING (pool #033)

При чтении `PENDING_FOLLOWUPS.md` отдельно выцепить **протухшие** открытые пункты ([pool #033](../../../brain_matrica/cross-project-ideas/ideas/033-deferred-backlog-aging-retriage.md)): тег `stale`, либо открыто > 30 дней, либо `snooze ≥ 3` (конвенция меток — в шапке самого файла). Найденное вынести в отчёт (Шаг 6) с предложением **ре-триажа тремя исходами**: возобновить / переформулировать под текущий код / выкинуть (с причиной). Не возобновлять слепо. Пункты `parked` (сознательно отложены до явного условия) не всплывать, пока условие не наступило.

### 3.2. Напоминание о дистилляции Корпуса (заказ владельца 2026-07-14)

Прочитать верхнюю строку таблицы [`docs/ops/DISTILL_LOG.md`](../../docs/ops/DISTILL_LOG.md).
Облачной рутины-дистиллятора больше нет — дистилляция делается только из чата по
[`/distill`](distill.md). Если файла нет или прошло < 7 дней — молчать.

Прошло **> 7 дней** — **не напоминать сразу, а сперва проверить, есть ли сырьё**
(read-only, ~2 секунды, требует того же подтверждения SSH, что и Шаг 5 — при
`--no-prod` или отказе пропустить и напомнить по дате, пометив «без проверки корпуса»):

```bash
ssh sarafan 'cd ~/SETKA && CLASSIFIER_INGEST_KEY=$(sudo -n grep -m1 "^CLASSIFIER_INGEST_KEY=" /etc/setka/classifier-routine-key.txt | cut -d= -f2- | tr -d "[:space:]") python3 scripts/classifier_routine.py corrections --limit 200 --days <N> --out /tmp/distill_probe'
```

`<N>` — **дней с последней дистилляции минус один** (обоснование — в [`/distill`](distill.md)).

- `count ≥ 10` (порог осмысленности из `/distill`) → напоминание в отчёт (Шаг 6):
  «⏰ Дистилляция не делалась N дней, накопилось K коррекций — запустить `/distill`?».
- `count < 10` → **молчать**, сколько бы дней ни прошло.

**Почему условие, а не таймер.** Сырьё дистилляции — ручной разбор ленты оператором,
а таймер тикает сам. Триггер и реальное условие обновляются разными механизмами, и
такой сигнал деградирует до частоты ручной половины, то есть до шума: напоминание
начинает приходить каждую сессию независимо от того, есть ли что дистиллировать.
Тот же класс отказа, что у меток старения в
[`PENDING_FOLLOWUPS.md`](../../docs/PENDING_FOLLOWUPS.md) (дата считалась
автоматически, статус писался рукой — pool #033, ре-триаж 2026-07-27).

## Шаг 4. Sanity-check локального окружения (параллельно)

Только чтения:

- `Glob` `venv/Scripts/python.exe` или `venv/bin/python` — есть ли venv в текущем worktree.
- Если venv есть — быстрая discovery: `.\venv\Scripts\python.exe -m pytest --co -q 2>&1 | tail -5` (или `./venv/bin/python -m pytest --co -q | tail -5` на Linux). Число — порядка указанного в `AGENTS.md` §Состояние проекта; резкое падение = сломанный сбор.
- `Glob` `database/migrations/*.sql` — посмотреть свежесть последней миграции (`git log -1 --format='%cs %s' -- database/migrations/`).

Если venv нет — отметить в отчёте, **не создавать сам**: команда создания — в [`docs/START_HERE.md`](../../docs/START_HERE.md).

## Шаг 5. Прод-probe (опционально — пропускается при `--no-prod`)

Если `$ARGUMENTS` содержит `--no-prod` — пропустить шаг.
Иначе — **через `AskUserQuestion` спросить**: «Делать SSH-probe прода? (auto-mode classifier требует подтверждения)». Опции:

- «Да, проверь прод» — выполнить probe
- «Нет, пропустить» — двигаться к отчёту
- «Дай полный доступ ssh sarafan на эту сессию» — отметить и работать дальше без вопросов

При «да» — параллельный SSH-probe (быстрый, безопасный, read-only):

```bash
ssh -o ConnectTimeout=10 sarafan "systemctl is-active setka setka-celery-worker setka-celery-beat setka-vk-bot" 2>&1
ssh -o ConnectTimeout=10 sarafan "curl -s -o /dev/null -w 'health: %{http_code} in %{time_total}s\n' --max-time 10 http://127.0.0.1:8000/api/health/full" 2>&1
ssh -o ConnectTimeout=10 sarafan "cd ~/SETKA && git log --oneline -3" 2>&1
```

Если что-то не 200 / не active — отметить в отчёте, **но не диагностировать без запроса пользователя**.

## Шаг 6. Отчёт пользователю

Структура (на русском; ровно столько, сколько нужно для решения «продолжаем нитку?»):

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
