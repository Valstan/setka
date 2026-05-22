# Pending follow-ups

Открытые задачи, техдолги и идеи проекта SETKA. **Свежее сверху.**

**Приоритеты:**
- 🔴 **блокер** — прод сломан / нельзя двигаться дальше / безопасность
- ⏳ **в процессе** — начато, не дозавершено
- 🟡 **техдолг** — работает, но «костыль» / непрозрачно / повторение боли
- 🟢 **идея** — улучшение качества жизни, не критично

При закрытии — переноси запись в [`DEV_HISTORY.md`](DEV_HISTORY.md) (в текущий день).

---

## 🔴 Блокеры

_Сейчас нет._

---

## ⏳ В процессе

### Рефакторинг модуля уведомлений VK (этапы 1-5)

Все этапы закрыты:

2026-05-21:
- 0 — Fallback на user-token при VK error 15/27 + `keep_if_empty` в storage.
- 1 — Полный сбор комментариев: пагинация по offset, thread.items, `max_total_comments` 300→5000 safety cap.
- 2 — BaseVKChecker (DRY), удалён UnifiedNotificationsChecker и dead-code, окно 8-22 только в crontab.
- 3 — Storage history + виджет «активность за 24ч» (Chart.js) + API `/history` `/stats`.
- 4a-mini — `like_comment` от имени сообщества, mark-as-handled (7d), виджет «Горячие посты».
- 5 — Prometheus метрики + token-health watchdog с Telegram-alert и 6h cooldown.
- hot-fix-2 — VKClient.api_call propagates error_code + regex fallback в `_invoke`.

2026-05-22:
- 4b — Inline-reply на коммент (`wall.createComment` + `from_group`); AI-черновик через Groq (`llama-3.1-8b-instant`); шаблонные ответы на сообщения + CRUD-страница `/templates` + миграция 008; Telegram inline-кнопки с deep-link на `#section=...`. Доделано всё, что откладывалось.

Техдолги по audit'у token routing (продолжаются):

### 🆕 Техдолги по audit'у token routing

Закрыто 2026-05-21 (см. `DEV_HISTORY.md`):
- ✅ VK Captcha rate-limit — добавлен `GLOBAL_PUBLISH_INTERVAL_SECONDS=1.5` (class-var на VKPublisher).
- ✅ Косметика лога `via=community-token` после fallback — `_call_wall_post` теперь возвращает `(response, via_label)`, метка вычисляется по фактическому пути.
- ✅ wall.repost больше не пробует community-token (VK API физически не поддерживает) — добавлен `_USER_TOKEN_ONLY_METHODS={'wall.repost'}`.

Закрыто 2026-05-22 (см. `DEV_HISTORY.md`, этап 4b):
- ✅ Inline-reply на коммент из карточки `/notifications`.
- ✅ AI-черновик через Groq в модалке ответа (кнопка ✨).
- ✅ Шаблонные ответы на сообщения сообщества + CRUD-страница `/templates`.
- ✅ Telegram inline-кнопки с deep-link на `/notifications#section=...`.

Остаются:

- ~~**Удалить мёртвый код**~~ (закрыто 2026-05-22, см. DEV_HISTORY): `cross_region_repost.py` оказался уже удалён ранее; `correct_workflow` + `publish_digest_to_main_group` удалены целиком вместе с beat-entry `monitoring-hourly` и `tasks/correct_workflow_tasks.py`.
- ~~**Мигрировать старый `vk_publisher.py`**~~ (частично закрыто 2026-05-22): deprecated стек удалён (`modules/publisher/publisher.py`, `modules/scheduler/scheduler.py`, `tasks/publishing_tasks.py`, `tasks/test_info_tasks.py`, `modules/test_info_scheduler.py`, `web/api/workflow.py`, `scripts/test_full_workflow.py`). Остаётся живой `web/api/publisher.py` (UI `/publisher`) — он использует кастомные методы (`get_group_info`, `publish_aggregated_post`, `get_target_group_id`), которых нет в extended. Миграция требует либо расширения extended-API, либо переписывания endpoint'ов. Записано в 🟢 идеи.
- ~~**Глобальный rate-limit на parse-token VITA**~~ — закрыто 2026-05-22 (`GLOBAL_PARSE_INTERVAL_SECONDS=0.4` в `VKClient`, per-process per-token). Cross-process variant (через Redis) на случай multi-worker Celery — записан в 🟢 идеи.

_Все запланированные этапы (0, 1, 2, 3, 4a-mini, 4b, 5) закрыты. См. `DEV_HISTORY.md`._

---

## 🟡 Техдолги

### Запуск и окружение

- **`main.py:25` хардкодит `/home/valstan/SETKA/logs/app.log`.** Локально на Windows запустить приложение нельзя — только тесты. Стоит вынести путь в env (`LOG_PATH`) с дефолтом на прод-значение.
- **`venv` создаётся вручную в каждом worktree.** Нет скрипта `scripts/setup-dev.ps1` / `scripts/setup-dev.sh`, который бы делал `py -3.11 -m venv venv && .\venv\Scripts\pip install -r requirements.txt pytest pytest-asyncio`. Сейчас это память + ручная работа.

### Документация / разработка

- **Шаблон записи `DEV_HISTORY.md`** — нет явного шаблона в файле. Стоит добавить «как писать новую запись» в его шапку (формат заголовка, секции, ссылки на тесты).
- **Pre-commit и CI разъезжаются** — pre-commit зашит на `python3.12`, прод тоже 3.12, но локально для тестов 3.11. Иногда black/isort даёт разные стили между 3.11 и 3.12. Стоит зафиксировать одну версию для pre-commit (опционально через `pre-commit-config.yaml → language_version: python3.11`).

### Прод-операции

- **Применение SQL-миграций — ручное.** В `database/migrations/*.sql` есть нумерация (003..009 + legacy add_sentiment_fields). Частично закрыто 2026-05-22: 003+004 переписаны под идемпотентность (был копипаст-bug с дублированием + `CREATE TRIGGER` без IF NOT EXISTS), добавлен `database/migrations/README.md` с правилами для будущих миграций. Все 7 актуальных миграций теперь идемпотентны. Остаётся открытым `applied_migrations` runner — таблица учёта + Python-скрипт типа `scripts/migrate.py up`, который сам решает что применять. Это отдельная инфра-задача, помечена как 🟡.
- ~~**GRANT в миграциях / ALTER DEFAULT PRIVILEGES.**~~ Закрыто 2026-05-22 миграцией 009 (см. `DEV_HISTORY.md`). Будущие миграции не должны включать explicit `GRANT ALL ... TO setka_user` — default privileges выдаст их автоматически.
- **Auto-mode classifier блокирует SSH на прод.** Каждый новый чат требует подтверждения через `AskUserQuestion` или правки `settings.json`. Стоит решить раз и навсегда: либо добавить permission rule для `ssh setka-prod *` в `.claude/settings.json`, либо явно держать this-friction.

---

## 🟢 Идеи

### Удобство разработки

- **`/check`** — health-check одной кнопкой (pytest + prod systemd + curl + Celery). _(Сделано — см. [`.claude/commands/check.md`](../.claude/commands/check.md).)_
- **`/celery`** — состояние Celery: workers, beat, последние публикации, Redis cooldown. _(Сделано — см. [`.claude/commands/celery.md`](../.claude/commands/celery.md).)_
- **`/logs`** — параметризованный просмотр прод-логов. _(Сделано — см. [`.claude/commands/logs.md`](../.claude/commands/logs.md).)_
- **`/sql`** — psql на проде с подтверждением. _(Сделано — см. [`.claude/commands/sql.md`](../.claude/commands/sql.md).)_
- **Скрипт `scripts/dev-doctor.sh`** проверяет окружение: Python 3.11/3.12, venv, requirements, postgresql-клиент, ssh alias `setka-prod`, доступ к проду.
- **Hook на `git commit`**, который автоматически напоминает обновить `DEV_HISTORY.md` если коммит — `feat`/`fix`/`refactor`. Через `.git/hooks/prepare-commit-msg` или husky-аналог для Python.
- **Smoke-test после деплоя** — отдельный шаг в `/reliz`: парс одного тестового региона/темы в test-режиме без публикации (`scripts/test_parse_run.py`) и сравнение вывода с baseline.

### Наблюдаемость

- **Cross-process rate-limit на VKClient** — текущий per-process через `threading.Lock` (см. `GLOBAL_PARSE_INTERVAL_SECONDS=0.4`). Если когда-то Celery worker станет multi-process (`-c N` prefork) — нужен общий счётчик через Redis: `INCR setka:vk_ratelimit:<token>:<bucket>` или Lua-script с PEXPIRE.
- **Дашборд «состояние дайджестов»** — Grafana-панель: «когда последний раз публиковал регион X тему Y», на основе Redis-ключей `setka:digest_last_published:*` и логов `celery-worker.log`.
- **Алёрт в Telegram-бот**, если за последние 6 часов ни один регион не выпустил `novost`-дайджест (=что-то сломалось в beat).
- **Структурированные логи** — Celery worker сейчас пишет plain-text. Переход на JSON-логи + `journalctl -u setka-celery-worker -o json` + Loki/Prometheus упростит разбор инцидентов.

### Продукт

- **Мигрировать `web/api/publisher.py` на extended VKPublisher.** Endpoint живой (используется UI `/publisher`), но висит на старом `vk_publisher.VKPublisher` без community-tokens. Кастомные методы `get_group_info`, `get_target_group_id`, `publish_aggregated_post` отсутствуют в extended — нужно либо добавить их туда, либо переписать endpoint'ы под `publish_digest(text, group_id)`. См. DEV_HISTORY 2026-05-22 «Удаление deprecated publisher-стека».
- **UI «История публикаций» по регионам и темам** — `web/templates/publications.html`? Сейчас контроль идёт через VK-стены, нет своего удобного просмотра.
- **«Тёмный режим» для UI** — `/regions`, `/posts`, `/filtration` — длинные таблицы, ночью глаза вытекают.
- **`/regions/<code>/diagnostics`** — кнопка «прогнать пайплайн без публикации» в UI: видно, что отфильтровалось, что собрал aggregator, что попало бы в дайджест.
- **Полноценный Telegram-бот с webhook** — `bot.set_webhook` + `wall.createComment`/`messages.send` прямо из bot-handler без перехода в браузер. Сейчас (этап 4b) — URL-кнопки на `/notifications#section=...`, требуют один лишний клик. Это «фича роскоши», не блокер.
- **Per-region шаблоны ответов** — `message_templates.region_id NULL = all` + UI-фильтр. Пока шаблоны общие на все регионы (моде­ратор один).

---

## История пересечений

Если задача висела долго и пересекалась с несколькими сессиями — пиши тут историю переноса дат, чтобы было видно, что она «застряла».

_Сейчас пусто._
