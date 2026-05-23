# CLAUDE.md — entry point для AI-сессий SETKA

Этот файл — первое, что Claude должен прочитать в любой новой сессии разработки. Он подсказывает, **где взять контекст** и **как правильно работать**, не повторяя ошибки прошлых сессий.

---

## Язык общения

Все финальные ответы, сводки, объяснения и рекомендации пользователю — **на русском**.
Внутреннее рассуждение, код, комментарии в коде, commit-messages, идентификаторы — на английском (так в проекте).

---

## Источники правды (читать в начале каждой сессии)

| Файл | Что в нём |
|---|---|
| [`docs/START_HERE.md`](docs/START_HERE.md) | Быстрый старт, сервисы, команды на проде, чек-листы. |
| [`docs/AI_DEV_GUIDE.md`](docs/AI_DEV_GUIDE.md) | Полный архитектурный гайд: модули, потоки данных, типизация, антипаттерны. |
| [`docs/DEV_HISTORY.md`](docs/DEV_HISTORY.md) | Хронология изменений, свежее сверху. Что было сделано и почему. |
| [`docs/PENDING_FOLLOWUPS.md`](docs/PENDING_FOLLOWUPS.md) | Открытые задачи и техдолги с приоритетами 🔴⏳🟡🟢. |
| [`docs/REMOTE_ACCESS.md`](docs/REMOTE_ACCESS.md) | Прод-доступ — **только SSH** через `setka-prod`. MCP не использовать. |
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
4. **PR** — `gh pr create` с Summary + Test plan. После явного OK пользователя на diff — `gh pr merge --squash --delete-branch`.
5. **Релиз на прод** — `/reliz` ведёт через: обновление `DEV_HISTORY.md` и `PENDING_FOLLOWUPS.md` → commit → push на feature-ветку → `gh pr create` → review → merge → SSH `git pull` → миграции (если есть) → `systemctl restart` → curl health. Один шаг = один диалог.
6. **Закрытие сессии** — `/finish`. Проверяет, что `DEV_HISTORY.md` обновлён, что открытые/новые задачи отражены в `PENDING_FOLLOWUPS.md`. Опционально — commit + PR без deploy.

---

## Slash-команды

| Команда | Назначение |
|---|---|
| [`/start`](.claude/commands/start.md) | Открыть сессию: git fetch, прочитать SoT, прод-probe, отчёт. |
| [`/check`](.claude/commands/check.md) | Health-check одной кнопкой: pytest + prod systemd + curl + Celery. |
| [`/celery`](.claude/commands/celery.md) | Состояние Celery: workers, beat, последние публикации, Redis cooldown. |
| [`/logs`](.claude/commands/logs.md) | Просмотр прод-логов: `app`/`worker`/`beat`/`nginx` с `--grep` и `--since`. |
| [`/sql`](.claude/commands/sql.md) | psql на проде с обязательным подтверждением для DML. |
| [`/reliz`](.claude/commands/reliz.md) | Релиз: DEV_HISTORY+PENDING → commit → push → prod pull → миграции → restart → проверки. |
| [`/finish`](.claude/commands/finish.md) | Закрыть сессию: проверка DEV_HISTORY/PENDING, uncommitted, предложить /reliz. |

---

## Правила, которые НЕ менять

### Прод-доступ — только SSH
- Прод-хост в `~/.ssh/config` — `setka-prod` (`/home/valstan/SETKA`).
- **НЕ использовать MCP-серверы IDE** для деплоя/диагностики SETKA. Они путают разные VPS.
- Перед любой удалённой командой убедиться, что попал в SETKA: `ssh setka-prod 'test -f /home/valstan/SETKA/main.py && echo OK_SETKA'`.
- Auto-mode classifier Claude Code блокирует SSH-команды на прод как «Production Reads» — нужно явно подтверждать через `AskUserQuestion` либо разрешать через `settings.json` для конкретной сессии.

### Безопасность
- Секреты — **только** в `/etc/setka/setka.env` на VPS. Никогда не коммитить, не писать в чат.
- VK-токены собираются по префиксу `VK_TOKEN_<NAME>` (см. `config/runtime.py`).
- Любая destructive операция на проде (`ALTER`, `DROP`, `systemctl stop`, `rm`) — через `AskUserQuestion`.

### Документация
- При значимых изменениях (что-то меняющее поведение пайплайна / фильтров / публикации) — **обязательно** новая запись сверху в `docs/DEV_HISTORY.md`. Шаблон есть в самом файле.
- Открытые задачи и техдолги — в `docs/PENDING_FOLLOWUPS.md` с приоритетами 🔴⏳🟡🟢. При закрытии переносить в `DEV_HISTORY.md`.

### Локальная разработка
- ОС: Windows 11, PowerShell 5.1. Bash доступен через инструмент `Bash`.
- Worktree: `.claude/worktrees/<имя>` на отдельной ветке.
- Python: `py -3.11` локально для тестов (preferred), `py -3.12` тоже OK (=прод). `scripts/setup-dev.ps1` сам выбирает 3.11 → 3.12 → дефолтный `py`. venv в корне worktree.
- Запуск тестов: `.\venv\Scripts\python.exe -m pytest tests/ -q` (или через активированный venv).
- **`main.py` локально** — теперь работает через env `LOG_PATH` (см. `main.py` lifespan). Полный запуск требует Postgres + Redis локально; обычно достаточно тестов.

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
Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

---

## Полезные ad-hoc команды

```bash
# health прода
ssh setka-prod "curl -s http://127.0.0.1:8000/api/health/full"

# статус сервисов
ssh setka-prod "systemctl status setka setka-celery-worker setka-celery-beat --no-pager | head -50"

# свежий лог worker
ssh setka-prod "tail -100 /home/valstan/SETKA/logs/celery-worker.log"

# какие регионы публиковали в текущем часу (Redis cooldown)
ssh setka-prod "redis-cli --scan --pattern 'setka:digest_last_published:*' | sort"

# pg_dump прод-БД (на ssh-host, дальше scp)
ssh setka-prod "sudo -u postgres pg_dump -Fc setka > /tmp/setka-$(date +%Y%m%d).dump"
```

---

## Когда что-то идёт не так

- **Прод 502 / health не отвечает** → `ssh setka-prod "journalctl -u setka -n 100 --no-pager"`. Чаще всего — `setka.service` упал, нужен `systemctl restart`.
- **Дайджесты не выходят** → проверить через `/celery`: жив ли beat, нет ли регионов на cooldown, нет ли ошибок в `celery-worker.log`.
- **`pytest` падает локально** → проверить, что worktree свежий (`git pull origin <ветка>`), venv обновлён (`pip install -r requirements.txt`), есть `pytest pytest-asyncio`.
- **Миграция не применилась** → SQL-файлы в `database/migrations/*.sql`, применяются вручную через `ssh setka-prod 'sudo -u postgres psql -d setka -f /home/valstan/SETKA/database/migrations/NNN_*.sql'`. Команда `/sql` это умеет.

---

**В сомнениях — спроси пользователя через `AskUserQuestion`, не делай предположений на проде.**
