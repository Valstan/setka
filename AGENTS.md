# AGENTS.md — entry point для AI-сессий SETKA

Этот файл — первое, что Codex должен прочитать в любой новой сессии разработки. Он подсказывает, **где взять контекст** и **как правильно работать**, не повторяя ошибки прошлых сессий.

---

## Язык общения

Все финальные ответы, сводки, объяснения и рекомендации пользователю — **на русском**.
Внутреннее рассуждение, код, комментарии в коде, commit-messages, идентификаторы — на английском (так в проекте).

---

## Источники правды (читать в начале каждой сессии)

| Файл | Что в нём |
|---|---|
| [`docs/SESSION_HANDOFF.md`](docs/SESSION_HANDOFF.md) | **Sticky-note между сессиями** — текущая активная нитка, следующий шаг, failed approaches. Перезаписывается через `/close_session`. |
| [`docs/START_HERE.md`](docs/START_HERE.md) | Быстрый старт, сервисы, команды на проде, чек-листы. |
| [`docs/AI_DEV_GUIDE.md`](docs/AI_DEV_GUIDE.md) | Полный архитектурный гайд: модули, потоки данных, типизация, антипаттерны. |
| [`docs/REGIONS_HIERARCHY.md`](docs/REGIONS_HIERARCHY.md) | Иерархия регионов `strana → oblast → raion`, словарь терминов, каскадная сводка. |
| [`docs/REGION_REFRESH_LOG.md`](docs/REGION_REFRESH_LOG.md) | **Журнал освежения регионов** — когда какой район/область освежался по канонам (добор/чистка доноров, новые фичи). Канон-чеклист + таблица приоритета + журнал событий. «Обновим следующий устаревший регион» → берём верх таблицы. |
| [`docs/adr/`](docs/adr/) | Architectural Decision Records — «почему именно так» (см. [ADR-0001](docs/adr/0001-archive-dev-history.md) про минимализм AI-docs 2026). |
| [`docs/PENDING_FOLLOWUPS.md`](docs/PENDING_FOLLOWUPS.md) | Открытые задачи и техдолги с приоритетами 🔴⏳🟡🟢. |
| [`docs/REMOTE_ACCESS.md`](docs/REMOTE_ACCESS.md) | Прод-доступ — **только SSH** через `setka`. MCP не использовать. |
| [`docs/OPERATIONS.md`](docs/OPERATIONS.md) | Эксплуатация, systemd, логи, troubleshooting. |
| [`docs/TESTING.md`](docs/TESTING.md) | pytest, фикстуры, как гонять тесты. |
| [`docs/paths.md`](docs/paths.md) | Карта файлов и API endpoints. |

Slash-команда `/start` всё это читает автоматически и выдаёт сводку.

---

## Интеграция с brain_matrica

setka управляется meta-репо [brain_matrica](../brain_matrica/) (стратегический hub для всех проектов @valstan). Связь — **асимметричная**: каждая сторона пишет **только в свой репо**, читает чужой через `git pull --ff-only` ([ADR-0001 v3](../brain_matrica/adr/0001-brain-projects-mailboxes.md) от 2026-05-23, [asymmetry-fix письмо](../brain_matrica/mailboxes/setka/from-brain/2026-05-23-mailbox-asymmetry-fix.md)).

| Направление | Кто пишет | Куда | Кто читает |
|---|---|---|---|
| brain → setka | brain (в своём репо) | `brain_matrica/mailboxes/setka/from-brain/*.md` | setka через `cd ../brain_matrica && git pull --ff-only` (read-only) |
| setka → brain | **setka (в своём репо)** | **`setka/mailbox/to-brain/*.md`** | brain через `cd ../setka && git pull --ff-only` (read-only) |

| Аспект | Где / как |
|---|---|
| Протокол mailbox | [ADR-0001 v3](../brain_matrica/adr/0001-brain-projects-mailboxes.md) (асимметричная схема; `compliance` field: `suggest`/`recommend`/`mandate` = MAY/SHOULD/MUST по RFC 2119) |
| PR-only flow | [ADR-0002](../brain_matrica/adr/0002-pr-only-flow-no-direct-push.md) — direct push в `main` запрещён, кроме hot-fix аварии |
| Постулаты | [POSTULATES.md](../brain_matrica/docs/POSTULATES.md) |
| Запись о setka в brain | [projects/setka.md](../brain_matrica/projects/setka.md) (read-only с моей стороны — пинговать через `mailbox/to-brain/` feedback) |
| Свой mailbox | [`mailbox/to-brain/`](mailbox/) (только этот канал для исходящих в brain) |

**Жизненный цикл письма от brain:**

1. `/start` Шаг 0: `cd ../brain_matrica && git pull --ff-only` → сканить `mailboxes/setka/from-brain/*.md` (без `DRAFTS/` и `ARCHIVE/`), доклад в формате `[urgency COMPLIANCE]`.
2. Пользователь решает обработать → применяем директиву согласно compliance.
3. **Ответ** (acknowledgement / feedback / report) — в **свой репо** `setka/mailbox/to-brain/YYYY-MM-DD-<slug>.md`, коммит в setka через PR.
4. **Архивацию исходных писем делает brain у себя** — не моя зона.

**Проактивный шеринг находок (рефлекс #009):** значимые **переносимые** находки (скилл / фича / паттерн / решённая нетривиальная боль) сам отправляю в мозг через `mailbox/to-brain/` — не дожидаясь просьбы. Условный шаг + анти-спам-фильтр (значимо ∧ переносимо ∧ неочевидно) — в [`/close_session`](.Codex/commands/close_session.md) Шаг 5.5 (pool [#009](../brain_matrica/cross-project-ideas/ideas/009-share-findings-reflex.md)). По умолчанию — молчим.

**Консультация с библиотекой Мозга (рефлекс #014):** read-сторона того же шкафа, что и #009 (тот пишет, этот читает). **Не** безусловный шаг `/start` (token economy, [ADR-0003](../brain_matrica/adr/0003-token-economy-principles.md)) — а **условный триггер**, срабатывает ровно в двух случаях:
1. **Перед вводом нового/нетривиального** (паттерн, инструмент, инфра-подход, миграция данных, кросс-cutting рефактор) — *до* проектирования бегло просмотреть [`../brain_matrica/cross-project-ideas/INDEX.md`](../brain_matrica/cross-project-ideas/INDEX.md) + [`../brain_matrica/tech-radar/INDEX.md`](../brain_matrica/tech-radar/INDEX.md): нет ли готового опыта.
2. **При незнакомой грабле инструмента/инфры/деплоя** (не доменный баг, а «почему CI / Payload / git / VK так себя ведёт») — *до* долгого дебага грепнуть [`../brain_matrica/cross-project-ideas/GOTCHAS.md`](../brain_matrica/cross-project-ideas/GOTCHAS.md) по симптому.

Нашёл релевантное → переиспользуй (и при желании отпишись в `mailbox/to-brain/`, что применил). Не нашёл → продолжай как обычно. `git pull --ff-only` brain'а уже делается на `/start`, повторно не платим. **Тишина = норма** (триггер не сработал → 0 лишних чтений). Pool [#014](../brain_matrica/cross-project-ideas/ideas/014-consult-library-reflex.md).

**Тактика напрямую (ADR-0007 мозга, 2026-07-05):** любой sibling-репо (`../<project>/`) можно читать **read-only** напрямую, без письма в мозг (`git pull --ff-only` перед чтением): API-контракты, docs, handoff соседа, его `mailbox/to-brain/`. Эвристика: «нужно ЗНАТЬ про соседа → читай сам; нужно, чтобы сосед/экосистема что-то СДЕЛАЛИ или ЗАПОМНИЛИ → письмо в мозг». Начал **зависеть** от чужого API/формата (интеграция, не разовое чтение) → сообщить мозгу письмом (прочитанное — не контракт).

**Что нельзя:**
- ❌ Писать в `brain_matrica/` (ни в `mailboxes/setka/to-brain/`, ни в `.last-seen`, ни в `ARCHIVE/`, ни куда-либо ещё). Доступ — только `git pull --ff-only`.
- ❌ Клонировать `brain_matrica` для записи; ходить в чужие mailbox'ы; удалять архивные письма у brain'а.

---

## Жизненный цикл задачи

1. **Старт сессии** — `/start`. Сводка: mailbox от brain_matrica, что нового на main, какие хвосты, состояние прода.
2. **Feature-ветка** — `git checkout -b <type>/<slug>` (`feat/`, `fix/`, `chore/`, `docs/`, `refactor/`). Direct push в `main` запрещён ([ADR-0002](../brain_matrica/adr/0002-pr-only-flow-no-direct-push.md)).
3. **Работа над фичей** — обычные правки кода. После них:
   - `pytest tests/ -q` — все 360+ тестов должны быть зелёными.
   - `pre-commit run --all-files` — black/isort/flake8.
4. **PR** — `gh pr create` с Summary + Test plan. **Описательное тело PR заменяет старую DEV_HISTORY** ([ADR-0001](docs/adr/0001-archive-dev-history.md)) — что меняли, почему, какие тесты, какое применение на проде. **Авто-мерж под гейтами** ([#027](#автономия-под-гейтами-027)): зелёные `pre-commit` + `pytest` + CI → `gh pr merge --squash --delete-branch` без ручного «окей» (черта — destructive прод-операции с данными, см. ниже).
5. **Релиз на прод** — `/reliz` ведёт через: обновление `PENDING_FOLLOWUPS.md` (если есть новые техдолги) → commit с описательным сообщением → push на feature-ветку → `gh pr create` с полным описанием → review → merge → SSH `git pull` → миграции (если есть) → `systemctl restart` → curl health. Один шаг = один диалог.
6. **Закрытие сессии** — `/close_session` (**единственная** команда закрытия). Коммитит и пушит **всю** работу (код + доки) на GitHub через PR, обновляет `SESSION_HANDOFF.md` + `PENDING_FOLLOWUPS.md`, проверяет sync-гейт (`scripts/git_sync_check.sh --gate`) — сессия не считается закрытой, пока всё не на `origin`. Деплой — отдельно через `/reliz`.

---

## Автономия под гейтами (#027)

Brain-мандат [#027](../brain_matrica/cross-project-ideas/ideas/027-gate-replaced-autonomy.md) (2026-06-06): **человеческий «окей на дифф/мерж/деплой» заменён автоматическими гейтами.** Не «убрать проверку», а «заменить ритуал на машинную проверку»: автономия безопасна ⟺ гейты сильны. Настройка живёт в коммитимом [`.Codex/settings.json`](.Codex/settings.json) (`defaultMode: auto` + ярусные `permissions.allow`/`deny`).

**Ярусы по риску:**

| Операция | Режим | Гейт = подтверждение |
|---|---|---|
| Правки файлов, ветки, коммиты | **авто** | — |
| Push feature-ветки, `gh pr create`, **`gh pr merge --squash`** | **авто** | `pre-commit run --all-files` (black/isort/flake8) + `pytest tests/ -q` + CI зелёные |
| Деплой не-схемных правок (`git pull` + `systemctl restart` + health-curl) | **авто под гейтом** | `/reliz` smoke + health 200; деплои **сериализованы** (не внахлёст) |
| **Миграции на проде с данными, `ALTER`/`DROP`/`DELETE`/`UPDATE`, ротация токенов, необратимые trigger'ы** | **подтверждать в том же ходе** | **эту черту не пересекаем — гейт остаётся человеческим** ([#025](../brain_matrica/cross-project-ideas/ideas/025-destructive-prod-confirm-same-turn.md), `AskUserQuestion`) |

**Что заблокировано машинно** (`permissions.deny`, побеждает всё): `git push --force`, `git push origin main/master` (только через PR, [ADR-0002](../brain_matrica/adr/0002-pr-only-flow-no-direct-push.md)), `git reset --hard`, `rm -rf`.

**Важный нюанс инструмента:** правила Codex сопоставляются **по префиксу** и **по имени инструмента** (`Bash(...)` ≠ `PowerShell(...)` — в `settings.json` есть оба набора, т.к. Windows-машины гоняют команды через PowerShell). Префикс **не различает** read-only `ssh setka "..."` от destructive `ssh setka "...psql DROP..."` (дискриминатор внутри кавычек). Поэтому **гейт destructive-прода остаётся поведенческим** (`AskUserQuestion` перед любой необратимой прод-операцией) — он не выражается deny-правилом, и нарушать его нельзя. `/sql` гейтит DML, `/reliz` ведёт миграции под подтверждением.

> NB ([#018](../brain_matrica/cross-project-ideas/ideas/018-liveness-watchdog-durable-heartbeat.md)): раз мерж/деплой идут автономнее, «приём/публикация молча встала» дороже — держать heartbeat-watchdog и его вывод в дашборд живым.

---

## Slash-команды

| Команда | Назначение |
|---|---|
| [`/start`](.Codex/commands/start.md) | Открыть сессию: git fetch, прочитать SoT, прод-probe, отчёт. |
| [`/check`](.Codex/commands/check.md) | Health-check одной кнопкой: pytest + prod systemd + curl + Celery. |
| [`/celery`](.Codex/commands/celery.md) | Состояние Celery: workers, beat, последние публикации, Redis cooldown. |
| [`/logs`](.Codex/commands/logs.md) | Просмотр прод-логов: `app`/`worker`/`beat`/`nginx` с `--grep` и `--since`. |
| [`/sql`](.Codex/commands/sql.md) | psql на проде с обязательным подтверждением для DML. |
| [`/reliz`](.Codex/commands/reliz.md) | Релиз: PENDING (если нужно) → commit с описанием → push → PR с полным телом → prod pull → миграции → restart → проверки. |
| [`/close_session`](.Codex/commands/close_session.md) | **Единственная команда закрытия сессии.** Закоммитить+запушить ВСЁ (код+доки) на GitHub через PR, обновить SESSION_HANDOFF.md + PENDING, sync-гейт «всё ли на origin». Триггерится и фразами «закрой сессию [разработки]», «заверши сессию». |
| [`/obriv`](.Codex/commands/obriv.md) | Восстановление после обрыва связи: реконструировать состояние из git/PR (ground truth), проверить целостность записанных файлов, реконсилировать последнее действие, перепроверить гейты (black/isort/flake8 + pytest), продолжить нить — **не переделывая сделанное**. Кросс-проектный мандат brain (pool #021). |

---

## Правила, которые НЕ менять

### GitHub — источник истины между машинами
- Пользователь работает на **разных компьютерах** (днём один, вечером другой). GitHub (`origin`) — единственный общий источник истины. Версии разъезжаются, если работа осталась незапушенной на одной машине.
- **Никогда не оставляй сессию с несинхронизированной работой.** Закрытие сессии = всё закоммичено и **запушено** на `origin`. Это делает `/close_session` (единственная команда закрытия) — у неё жёсткий sync-гейт `scripts/git_sync_check.sh --gate` (exit 1, пока дерево не чистое и не запушено).
- При входе в сессию SessionStart-хук (`scripts/git_sync_check.sh --warn`, прописан в `.Codex/settings.json`, коммитится и разъезжается на все машины) предупреждает, если на этой машине осталась несинхронизированная работа или `origin` ушёл вперёд (другая машина запушила → нужен `git pull`).
- **Естественная фраза** «закрой сессию», «закрой сессию разработки», «заверши сессию», «закрываемся» → запускать `/close_session`.
- Авто-архивацию сессий (Codex Desktop → вкладка **Cowork** → «Classify session states») при желании отключить вручную — это UI-настройка, не ключ `settings.json`; но sync-гейт и SessionStart-хук защищают независимо от неё.

### Прод-доступ — только SSH
- Прод-хост в `~/.ssh/config` — `setka` (`/home/valstan/SETKA`).
- **НЕ использовать MCP-серверы IDE** для деплоя/диагностики SETKA. Они путают разные VPS.
- Перед любой удалённой командой убедиться, что попал в SETKA: `ssh setka 'test -f /home/valstan/SETKA/main.py && echo OK_SETKA'`.
- Auto-mode classifier Codex блокирует SSH-команды на прод как «Production Reads» — нужно явно подтверждать через `AskUserQuestion` либо разрешать через `settings.json` для конкретной сессии.

### Безопасность
- Секреты — **только** в `/etc/setka/setka.env` на VPS. Никогда не коммитить, не писать в чат.
- VK-токены собираются по префиксу `VK_TOKEN_<NAME>` (см. `config/runtime.py`).
- Любая destructive операция на проде (`ALTER`, `DROP`, `DELETE`, `UPDATE`, `systemctl stop`, `rm`, ротация токенов) — через `AskUserQuestion`. Это **человеческий гейт #025**, который **остаётся** даже под автономией #027 (см. [«Автономия под гейтами»](#автономия-под-гейтами-027)): префиксные `deny`-правила не различают destructive `ssh setka "..."` от read-only, поэтому черту держит поведение, а не `settings.json`.

### Документация
- **Хронология изменений живёт в git** (`git log --oneline -20`) — Conventional Commits + описательное тело коммита + PR Summary заменяют старый `docs/DEV_HISTORY.md` ([ADR-0001](docs/adr/0001-archive-dev-history.md)).
- При значимом изменении — описательный commit message ([Conventional Commits](https://www.conventionalcommits.org)) с указанием: что меняли, почему, какие тесты, как применять (миграция? restart? оба?). PR description расширяет это для контекста ревью / истории.
- **Уроки и failed approaches** (что попробовали и отбросили) — в `docs/SESSION_HANDOFF.md` (секция «Failed approaches этой нитки»), не в commit (там только успехи).
- **Архитектурные решения** с «почему именно так» — `docs/adr/` (новый файл `NNNN-short-title.md` с фронтматтером).
- **Открытые задачи и техдолги** — в `docs/PENDING_FOLLOWUPS.md` с приоритетами 🔴⏳🟡🟢. При закрытии — удалять строку (или пометить ~~strikethrough~~ если кратко зафиксировать «закрыто в PR #N»).

### Локальная разработка
- ОС: Windows 11, PowerShell 5.1. Bash доступен через инструмент `Bash`.
- Worktree: `.Codex/worktrees/<имя>` на отдельной ветке.
- Python: `py -3.11` локально для тестов (preferred), `py -3.12` тоже OK (=прод). `scripts/setup-dev.ps1` сам выбирает 3.11 → 3.12 → дефолтный `py`. venv в корне worktree.
- Запуск тестов: `.\venv\Scripts\python.exe -m pytest tests/ -q` (или через активированный venv).
- **`main.py` локально** — логи идут в stderr через `logging.basicConfig` (LOG_LEVEL env, дефолт `INFO`). На проде systemd редиректит stdout/stderr в `/home/valstan/SETKA/logs/uvicorn_production.log`. Полный запуск всё равно требует Postgres + Redis локально; обычно достаточно тестов.

---

## Стиль коммитов и веток

Conventional commits + соответствующий префикс ветки:
- `feat(scope):` / `feat/<slug>` — новая фича
- `fix(scope):` / `fix/<slug>` — баг-фикс
- `refactor(scope):` / `refactor/<slug>` — рефакторинг без смены поведения
- `docs:` / `docs/<slug>` — только документация
- `chore:` / `chore/<slug>` — обслуживание (deps, configs)
- `test:` / `test/<slug>` — только тесты

Slug — kebab-case, описательный. Ветка удаляется после merge (`gh pr merge --delete-branch`). Force-push в feature-ветку разрешён, в `main` — никогда.

Тело коммита — что и почему. В конце:

```
Co-Authored-By: Codex Opus 4.7 (1M context) <noreply@anthropic.com>
```

---

## Полезные ad-hoc команды

```bash
# health прода
ssh setka "curl -s http://127.0.0.1:8000/api/health/full"

# статус сервисов
ssh setka "systemctl status setka setka-celery-worker setka-celery-beat --no-pager | head -50"

# свежий лог worker
ssh setka "tail -100 /home/valstan/SETKA/logs/celery-worker.log"

# какие регионы публиковали в текущем часу (Redis cooldown)
ssh setka "redis-cli --scan --pattern 'setka:digest_last_published:*' | sort"

# pg_dump прод-БД (на ssh-host, дальше scp)
ssh setka "sudo -u postgres pg_dump -Fc setka > /tmp/setka-$(date +%Y%m%d).dump"
```

---

## Когда что-то идёт не так

- **Прод 502 / health не отвечает** → `ssh setka "journalctl -u setka -n 100 --no-pager"`. Чаще всего — `setka.service` упал, нужен `systemctl restart`.
- **Сводки не выходят** → проверить через `/celery`: жив ли beat, нет ли регионов на cooldown, нет ли ошибок в `celery-worker.log`.
- **`pytest` падает локально** → проверить, что worktree свежий (`git pull origin <ветка>`), venv обновлён (`pip install -r requirements.txt`), есть `pytest pytest-asyncio`.
- **Миграция не применилась** → SQL-файлы в `database/migrations/*.sql`, применяются вручную через `ssh setka 'sudo -u postgres psql -d setka -f /home/valstan/SETKA/database/migrations/NNN_*.sql'`. Команда `/sql` это умеет.

---

**В сомнениях — спроси пользователя через `AskUserQuestion`, не делай предположений на проде.**
