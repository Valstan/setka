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

### 🌍 Big idea — модуль авто-регистрации регионов и сообществ

**MVP + recheck закрыты 2026-05-22** (см. `DEV_HISTORY.md`):
- ✅ Миграция 011: `community_candidates` + `regions.vk_city_id/center_city` + `communities.health_*` + composite индекс (region_id, vk_id).
- ✅ Backend MVP: `modules/discovery/{vk_search,ai_categorizer}.py`, `tasks/discovery_tasks.py`.
- ✅ Web API: `/api/discovery/{cities,trigger,candidates,candidates/{id},candidates/bulk}`.
- ✅ UI: `/regions/new` wizard с auto-resolve VK city, `/regions/<code>/discovery` с фильтрами / approve modal / bulk-actions.
- ✅ **Recheck (итерация 2)**: `modules/discovery/health_check.py` + `tasks.discovery_tasks.recheck_communities_for_region` / `recheck_all_active_regions`. Beat entry `discovery-recheck-weekly` (Mon 04:00 MSK). Telegram-alert по итогам. VK errors 15/18/100/203 → `dead`; пост старше `dormant_days` (default 60, override `region.config['dormant_days']`) → `dormant`; AI drift при confidence ≥ 70 → `changed_category` + `suggested_category`.
- ✅ +90 тестов всего по big idea (60 MVP + 30 recheck), 360/360 зелёных.

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

### Git / brain_matrica integration

- **Branch protection rules на GitHub для `main`.** [ADR-0002 §D](../../brain_matrica/adr/0002-pr-only-flow-no-direct-push.md) рекомендует включить после первого успешного PR на новой схеме. Настройки: Require PR before merging / Disallow force push / Disallow deletion. Делается один раз через `gh api -X PUT repos/Valstan/setka/branches/main/protection ...` или через web-UI на github.com. После включения попытки direct push в main будут отбиты технически (сейчас — только дисциплиной).

### Запуск и окружение

- ~~**`main.py:25` хардкодит `/home/valstan/SETKA/logs/app.log`.**~~ Закрыто ранее: `main.py:45` уже использует `os.getenv("LOG_PATH", "/home/valstan/SETKA/logs/app.log")` с safe-fallback на StreamHandler. Запись была устаревшей.
- ~~**`venv` создаётся вручную в каждом worktree.**~~ Закрыто ранее: есть `scripts/setup-dev.ps1` и `scripts/setup-dev.sh`. 2026-05-22 добавлено `pre-commit install` в оба скрипта — теперь свежий worktree сразу получает git-хук.
- **Хардкоды `/home/valstan/SETKA/logs/parser*` в `web/api/parsing.py` и `tasks/parsing_tasks.py`** (`OUTPUT_DIR`, `REPORTS_DIR`, `VIDEO_REPORT_PATH`, `os.makedirs` + `FileHandler` для `parser.log`). Аналогично `LOG_PATH` — вынести в env `PARSER_LOGS_DIR` (или общий `SETKA_LOGS_DIR`) с дефолтом на прод-путь. Не блокер: parser локально всё равно не запускается без VK-токенов и БД.

### Документация / разработка

- ~~**Шаблон записи `DEV_HISTORY.md`**~~ Закрыто ранее: шаблон в шапке `DEV_HISTORY.md` (раздел «Правила записи» + collapsible шаблон).
- ~~**Pre-commit и CI разъезжаются**~~ Закрыто ранее: `.pre-commit-config.yaml` фиксирует `default_language_version.python: python3.11` для всех хуков. Прод (3.12) и линтеры (3.11) дают одинаковый стиль.
- **Доочистка legacy flake8-ошибок** — 2026-05-22 расширили `.pre-commit-config.yaml extend-ignore` на E402, E501, E712, F841 и др. (357 нарушений в legacy). Это маскировка, не починка. Реальная зачистка — отдельная сессия: E712 в SQLAlchemy → truthy-check, E402 → переделать `sys.path.insert` через setuptools или `pyproject.toml`, E501 → ломать длинные строки, F841 → удалить unused locals.
- **Покрыть тестами восстановленные F821-ветки** — 2026-05-22 в `modules/core/config.py` (`ContextFactory.create_from_region`), `modules/publisher/digest_builder.py` (truncate_text branch), `utils/retry.py` (`retry_with_fallback` + `retry_with_circuit_breaker`) восстановлены пропавшие импорты. Видимо эти ветки не вызываются в runtime — иначе мы бы ловили `NameError` в проде. Стоит проверить что они вообще нужны (если dead code — удалить) или покрыть тестами.

### Прод-операции

- ~~**Применение SQL-миграций — ручное.**~~ Закрыто 2026-05-22: миграция 010 + `scripts/migrate.py` (stdlib, через `sudo -u postgres psql`). Сверяется с `applied_migrations`, применяет недостающее в транзакции вместе с INSERT-ом. Использование: `ssh setka-prod 'cd /home/valstan/SETKA && python3 scripts/migrate.py up'`.
- ~~**GRANT в миграциях / ALTER DEFAULT PRIVILEGES.**~~ Закрыто 2026-05-22 миграцией 009 (см. `DEV_HISTORY.md`). Будущие миграции не должны включать explicit `GRANT ALL ... TO setka_user` — default privileges выдаст их автоматически.
- ~~**Auto-mode classifier блокирует SSH на прод.**~~ Закрыто 2026-05-22: `.claude/settings.json` с `permissions.allow: ["Bash(ssh setka-prod:*)"]` (закоммичен в репо через `!.claude/settings.json` в `.gitignore`). Destructive-операции по-прежнему через `AskUserQuestion` — это политика CLAUDE.md, не permissions.

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

- ~~**🌍 Модуль авто-регистрации регионов и сообществ (big idea).**~~ MVP закрыт 2026-05-22 (см. ⏳ выше — осталась weekly recheck). Описание ниже сохранено для контекста второй итерации.
  Сейчас новый район добавляется вручную: сам регион в `regions` (код, имя, neighbors, vk_group_id главной ИНФО-группы), потом руками искать все паблики района ВКонтакте и добавлять в `communities` с тематикой (`admin`, `novost`, `reklama`, `sosed`, `kultura`, `sport`, `detsad`, …). Долго, при 12 действующих регионах терпимо, но ручной труд масштабируется плохо.

  **Что должен уметь модуль:**
  1. **Wizard добавления нового региона** — UI-страница `/regions/new`:
     - Поля: код, название, центр-город (для VK geo-search), neighbors (multi-select из существующих регионов), VK-группа главной ИНФО-страницы (опционально, можно создать позже).
     - На submit — `INSERT INTO regions` + сразу запускает discovery-таску.
  2. **VK discovery таска** (Celery): для региона ищет сообщества через комбинацию:
     - `groups.search(q="<город>", count=1000, country=1, city=<vk_city_id>)` — geo-search.
     - `groups.search(q="<город> новости")`, `q="<город> объявления"`, `q="<район> ДТП"` и т.д. — keyword search по списку тем.
     - Чтение VK-стены `wall.get(owner_id=<главная_группа>)` за последний месяц — какие группы репостит сама ИНФО-страница (это уже валидные «партнёры»).
     - Дедупликация по `vk_id`, исключение уже-добавленных.
     Сохраняет результаты в новую таблицу `community_candidates` со статусом `pending`.
  3. **AI-подсказка тематики** — для каждого кандидата Groq получает: название, screen_name, описание (`groups.getById(fields=description,members_count,activity,status`)) + 5 последних постов (`wall.get(count=5)`). Возвращает predicted category + confidence (0-100) + reasoning в 1 фразу. Аналогично — short flag «эта группа выглядит как ИНФО-страница» (нужно поставить как `vk_group_id` региона).
  4. **UI «Найдено N кандидатов»** — на странице `/regions/<code>/discovery`:
     - Каждый кандидат: аватарка, ссылка на VK, AI-категория с возможностью переопределить через dropdown, превью первого поста, кнопки «Добавить» / «Отклонить» / «Отложить».
     - Bulk-операции: «Добавить все с confidence > 70» / «Отклонить все рекламные» / «Только админ-группы».
  5. **Периодическая перепроверка существующих регионов** — Celery beat `weekly`:
     - Для каждого региона повторяет discovery, новые-неизвестные кандидаты → в `community_candidates`.
     - Для каждого уже-добавленного `Community.is_active=True`: пуллит `wall.get(count=1)`. Нет постов > N дней (60? настройка региона) → флаг `health_status='dormant'`. AI-проверка последних 10 постов — если категория сильно сместилась → `health_status='changed_category', suggested_category=...`. Группа удалена/заблокирована (VK error 15/100/203) → `health_status='dead'`.
     - Алёрт в Telegram: «по региону `mi`: 3 новых кандидата, 1 мёртвая группа, 2 сменили тематику».
  6. **Удаление/архивация**: модератор одним кликом помечает мёртвую группу `is_active=False` (НЕ удаляет — историю постов нужно сохранить). UI кнопка «Объединить с другим регионом» для случая «группа уехала из района».

  **Что нужно подсобрать перед стартом:**
  - Миграция: `community_candidates (id, region_id, vk_id, name, screen_name, ai_category, ai_confidence, ai_reasoning, status: pending|approved|rejected|deferred, created_at)`, + `communities.health_status (active|dormant|changed_category|dead)` + `communities.last_post_at` + `communities.checked_at`.
  - VK `groups.search` лимит — ~1000/сутки на токен, надо учесть в global rate-limit.
  - VK `country=1` (Россия) + `city=<vk_city_id>`. Список cities VK не возвращает дёшево, придётся захардкодить mapping регион→vk_city_id в `region_configs` (или ввести админ-поле в UI).
  - Groq: 5 постов × N кандидатов × cost — посчитать quota перед автозапуском.

  **Чек-лист для подзадач:**
  - [ ] Миграция 010 — `community_candidates` + `communities.health_*`.
  - [ ] `modules/discovery/vk_groups_search.py` — обёртка над `groups.search` с гео+ключевиками.
  - [ ] `modules/discovery/ai_categorizer.py` — Groq-prompt для категории + ИНФО-флага.
  - [ ] `tasks/discovery_tasks.py` — `run_discovery_for_region(region_id)` + `recheck_existing_communities()` + beat `weekly`.
  - [ ] `web/api/discovery.py` — CRUD кандидатов + bulk-операции.
  - [ ] `web/templates/region_discovery.html` + `region_new.html` + JS.
  - [ ] Тесты: vk groups.search mock, ai_categorizer happy/fallback, candidate CRUD, health-check логика (`dormant`/`dead`/`changed_category`).

- **Per-region keyword overrides для discovery** — сейчас `modules/discovery/vk_search.CATEGORY_KEYWORDS` единый. Добавить `region.config['discovery_keywords']` (опц. override) — некоторые районы знают свои спецслова («дтп», «происшествия» как отдельная тема, или микро-локалитет).
- **Quota guard для Groq в discovery** — посчитать сколько токенов уходит на discovery 100 кандидатов (~prompt 500t + response 100t = 60K tokens per region). Если станет дорого — кешировать ai-результаты per (vk_id, hash(description)).
- **`discovery-rediscover-monthly` beat-таска** — поверх weekly recheck'а добавить ежемесячный re-`run_discovery_for_region` по всем `Region.is_active=True`. Сейчас можно дёргать только ad-hoc из UI или Celery shell. Очевидный риск — Groq quota и VK groups.search limit (~1000/сутки на токен) при 12+ регионах. Альтернатива: разнести по дням недели (mi в понедельник, vp во вторник …) — `crontab(day_of_week=…)` per-region.
- **UI «changed_category» quick-action** — фильтр `/communities?health_status=changed_category` + кнопка «применить suggested_category → обновить Community.category одним кликом». Сейчас модератор должен руками: найти запись, скопировать `suggested_category` в `category`, сохранить. Один клик = меньше friction.
- **UI «История публикаций» по регионам и темам** — `web/templates/publications.html`? Сейчас контроль идёт через VK-стены, нет своего удобного просмотра.
- **«Тёмный режим» для UI** — `/regions`, `/posts`, `/filtration` — длинные таблицы, ночью глаза вытекают.
- **`/regions/<code>/diagnostics`** — кнопка «прогнать пайплайн без публикации» в UI: видно, что отфильтровалось, что собрал aggregator, что попало бы в дайджест.
- **Полноценный Telegram-бот с webhook** — `bot.set_webhook` + `wall.createComment`/`messages.send` прямо из bot-handler без перехода в браузер. Сейчас (этап 4b) — URL-кнопки на `/notifications#section=...`, требуют один лишний клик. Это «фича роскоши», не блокер.
- **Per-region шаблоны ответов** — `message_templates.region_id NULL = all` + UI-фильтр. Пока шаблоны общие на все регионы (моде­ратор один).

---

## История пересечений

Если задача висела долго и пересекалась с несколькими сессиями — пиши тут историю переноса дат, чтобы было видно, что она «застряла».

_Сейчас пусто._
