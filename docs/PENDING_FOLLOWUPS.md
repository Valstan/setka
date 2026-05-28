# Pending follow-ups

Открытые задачи, техдолги и идеи проекта SETKA. **Свежее сверху.**

**Приоритеты:**
- 🔴 **блокер** — прод сломан / нельзя двигаться дальше / безопасность
- ⏳ **в процессе** — начато, не дозавершено
- 🟡 **техдолг** — работает, но «костыль» / непрозрачно / повторение боли
- 🟢 **идея** — улучшение качества жизни, не критично

При закрытии — описательный commit message и/или PR description заменяют старую запись в DEV_HISTORY ([ADR-0001](adr/0001-archive-dev-history.md)). В этом файле — пометь строку `~~strikethrough~~` с короткой ссылкой «закрыто в PR #N» или просто удали. Деталей не хранить — они в `git log` + `gh pr view <N>`. Исторические ссылки на `DEV_HISTORY.md` ниже не правим — они указывают на снимки в `git show HEAD:docs/DEV_HISTORY.md` соответствующего периода.

---

## 🔴 Блокеры

_Сейчас нет._

- ~~**VK-токен VALSTAN не имеет scope `wall`/`likes`**~~ Закрыто 2026-05-26 (этот PR): попытка получить токен с `wall`+`groups` через четыре разных способа провалилась — VK 2026 (а) у публичных mobile-app_id (Kate Mobile, VK Messenger, VK Mobile) либо режет scope (отдаёт `[photos, email, ads, offline]`), либо привязывает токен к IP-адресу выпуска (error 5 `access_token was given to another ip address` при обращении с прод-VPS); (б) для своего Standalone-приложения VK закрыл новую форму создания (на dev.vk.com доступны только Мини-приложение / Игра / Плагин для сообществ), legacy URL `vk.com/editapp?act=create` тоже больше не показывает Standalone; (в) `likes.add` через community-token VK явно отказывается обслуживать с error 27 `Group authorization failed: method is unavailable with group auth`. **Решение**: кнопка ♥ в `/notifications` теперь — обычная ссылка-deeplink `https://vk.com/wall{owner}_{post}?reply={cid}&thread={cid}`, открывает пост в VK с фокусом на комменте, лайк ставится руками в VK. Backend endpoint `/api/notifications/comments/like` оставлен в коде на случай если когда-нибудь scope `wall` снова станет доступен для физлиц.
- ~~**Discovery trigger длится >180s — nginx обрывает клиента**~~ Закрыто 2026-05-25 ([PR #49](https://github.com/Valstan/setka/pull/49), `0edf84b`): trigger переведён на Celery + UI polling через `/api/discovery/task/{id}/status`. UI больше не виснет. Nginx полу-фикс 600s в `/etc/nginx/conf.d/setka.conf` остался — не мешает, можно при желании откатить на 180s.
- ~~**Groq API key возвращает 403 Forbidden**~~ Переведено в 🟡 техдолг 2026-05-26: discovery больше не зависит от Groq (PR #41 AI-batch через clipboard, PR #51 info-repost). Затрагивает только UX-фичу — AI-черновик ответа на VK-комменты в `modules/notifications/ai_drafter.py` (модератор пишет вручную). См. 🟡 ниже.

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

### Итерация 3 — localities-driven discovery + human-in-the-loop AI

Начато 2026-05-25 после жалобы на качество подбора кандидатов: discovery для `tuzha` возвращал крупные общегородские паблики Кирова/Татарстана без географической привязки к Тужинскому району. Корень — VK `groups.search` не строгий + сортировка по `members_count` усиливала перекос. Параллельно — `GROQ_API_KEY` 403 на проде (нет бюджета, см. 🔴 блокеры) → AI-категоризация перестала фильтровать релевантность.

Решение в 3 PR:

- ✅ **PR 1 — backend** ([#39](https://github.com/Valstan/setka/pull/39)): миграция 012 (`community_candidates.ai_is_relevant`), `vk_search.py` принимает `localities`/`keywords` из `region.config`, hard relevance-filter, сортировка `(matched_localities desc, members_count desc)`. +34 теста.
- ✅ **PR 2 — UI «Подготовка района»** ([#40](https://github.com/Valstan/setka/pull/40)): `/regions/<code>/prepare`. Два блока: localities (OSM Overpass auto-suggest + clipboard-prompt fallback) и discovery_keywords. API: `GET/PATCH /api/discovery/regions/{code}/config`, `GET /api/discovery/osm-localities`. +24 теста.
- ✅ **PR 3 — AI-batch через clipboard** ([#41](https://github.com/Valstan/setka/pull/41)): `/regions/<code>/discovery/ai-batch`. Чанки по 30, готовый prompt + clipboard, robust JSON parser. API: `GET /ai-batch`, `POST /ai-batch/apply`, `GET /ai-batch/status`. Badge «✓/✗ районный» + фильтр «скрыть нерелевантных» на discovery. +11 тестов.
- ✅ **Релиз на прод 2026-05-25**: HEAD `7ba2560`, миграция 012 применена, restart всех 3 сервисов, health 200. Endpoints `/regions/tuzha/prepare`, `/regions/tuzha/discovery/ai-batch` → 200. AI batch status: `total: 147 pending, processed: 0`.

⏳ **Осталось — практический smoke на tuzha** в браузере: `/regions/tuzha/prepare` → OSM auto-suggest или ChatGPT prompt → save → re-trigger discovery → должно отвалиться ~120/147 нерелевантных. Затем `/discovery/ai-batch` → прогнать через нейросеть → approve → commit. Это пользовательский шаг (нажимать кнопки), не код.

Зачем не ждать Groq: пользователь явно сказал — бюджета на API нет. Human-in-the-loop через clipboard бесплатно (юзер тратит свой ChatGPT/Claude.ai тариф), прозрачно, юзер видит точный prompt. Не масштабируется на еженедельный recheck — но и не нужно, recheck без AI работает (он смотрит health, не категоризацию), `changed_category` детекция временно отключена.

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

### Регионы и cross-region обмен новостями

- ~~**Регион «Кировская область Инфо» (kirov_obl) пустой — discovery не пополняет.**~~ Закрыто 2026-05-27 (этот PR): введена иерархия регионов `strana → oblast → raion` (миграция 015 с полями `regions.kind` + `regions.parent_region_id`). Создана запись `kirov_obl` с vk_group_id=-168170001 (https://vk.com/kirovskaya_info), 13 кировских районов привязаны через `parent_region_id`. Новый универсальный `modules/cascaded_digest.py` берёт по 5 свежих постов со стены главного сообщества каждого ребёнка, фильтрует рекламу/религию/дубли, публикует. Старая хрупкая логика «extract wall.refs из текста» удалена. Документация — `docs/REGIONS_HIERARCHY.md`.

- ~~**Cross-region обмен новостями («соседи репостят дайджесты друг другу») — мёртв.**~~ Закрыто 2026-05-28 ([PR #78](https://github.com/Valstan/setka/pull/78)): реанимирован **без дубляжа** — переиспользует движок `modules/cascaded_digest.run_cascaded_digest` с `source_mode="neighbors"`, тема `neighbors`, гейт `#Новости`. Источники — `Region.neighbors`. Тонкая обёртка `run_neighbor_digest`, задачи `share_neighbor_news`/`run_all_regions_neighbor_share`, beat `digest-share-neighbors-daily` (8:30). Мёртвый `modules/publisher/neighbor_sharing.py` удалён (один модуль). Тема `sosed` (парсинг `category="sosed"` внутри региона) не тронута.

- ~~**UI поле «соседи» отсутствует при создании/редактировании региона.**~~ Закрыто 2026-05-28 ([PR #79](https://github.com/Valstan/setka/pull/79)): multi-select «Соседи» в add/edit модалках на `/regions` (`web/templates/regions.html`), сохраняет коды в `Region.neighbors`. API уже поддерживал. _Браузер-верификация после деплоя ещё не сделана (см. SESSION_HANDOFF)._

- ~~**Bal/Kukmor — сироты без `parent_region_id` (Татарстан).**~~ Закрыто 2026-05-28 ([PR #77](https://github.com/Valstan/setka/pull/77)): миграция 016 создала `tatarstan_obl` (vk_group_id=-239149826, vk.com/tatar_stan_info), bal/kukmor привязаны. Beat-слоты `postopus-tatarstan-oblast-9/-19`. Для публикации нужен токен `COMM_239149826` (см. token routing ниже).

### Discovery

- ~~**Relevance-фильтр пропускает омонимные стемы**~~ Закрыто 2026-05-25 ([PR #44](https://github.com/Valstan/setka/pull/44), `a7bec89`): `_passes_relevance` с center-stem requirement + ≥2 distinct stems fallback + `_LARGE_GROUP_MEMBERS_THRESHOLD=50000` для крупных пабликов. 278 ложно-релевантных групп в БД для tuzha удалены SQL'ом.
- ~~**ChatGPT-prompt для localities — помечать омонимные нп**~~ Закрыто 2026-05-25 ([PR #47](https://github.com/Valstan/setka/pull/47), `d6249db`): prompt в `web/templates/region_prepare.html` теперь явно просит ChatGPT исключать топонимы, чьи названия совпадают с обычными русскими словами.
- ~~**Перевести `/api/discovery/trigger` на Celery + UI polls**~~ Закрыто 2026-05-25 ([PR #49](https://github.com/Valstan/setka/pull/49), `0edf84b`): endpoint возвращает `task_id`, UI polls `/api/discovery/task/{id}/status`. Worker через `tasks/discovery_tasks.run_discovery_for_region_async`.
- **🟡 Groq API key 403 Forbidden** (обнаружено 2026-05-24, переоценено 2026-05-26). Discovery теперь работает без Groq (PR #41 AI-batch через clipboard + PR #51 info-repost). Влияние осталось только на UX-фичу `modules/notifications/ai_drafter.py` — кнопка «✨ AI-черновик» в модалке ответа на VK-коммент возвращает ошибку, модератор пишет ответ вручную. Фикс не-кодовый: новый ключ на console.groq.com → `GROQ_API_KEY` в `/etc/setka/setka.env` → `sudo systemctl restart setka setka-celery-worker`. Если бюджета нет долго — можно скрыть кнопку или сделать prompt-clipboard fallback по аналогии с discovery.

### Token routing (2026-05-27)

- ~~**Valstan заблокирован VK до 2026-05-28T06:59:03**~~ Закрыто 2026-05-28: блокировка истекла, но токен оказался мёртв (пользователь сменил пароль аккаунта → токен инвалидирован). **Перевыпущен** новый через своё приложение `client_id=51421557` (scope `wall,groups,photos,docs,video,stories,pages,notifications,stats,market,offline`). Введён через `/tokens` (БД), синхронизирован в env, restart, `enable`, validate → `valid`. Парсинг и публикация снова на VALSTAN.
- ~~**kirov_obl публиковать не сможет** до создания community-token~~ Закрыто 2026-05-28: токен `COMM_168170001` (группа `vk.com/kirovskaya_info`) добавлен в БД, валиден. kirov_obl публикует через community-token.
- ~~**Парсинг и публикация читают токен из разных источников (env vs БД) — рассинхрон при ротации.**~~ Закрыто 2026-05-28 ([PR #76](https://github.com/Valstan/setka/pull/76)): `get_active_parse_tokens` берёт значение из БД (`vk_tokens`), а не env. Единый источник истины — управление через `/tokens`. Фильтр `validation_status != 'invalid'`. env `VK_TOKENS` — только аварийный DB-down fallback.
- 🟢 **`tatarstan_obl` ждёт community-token** для публикации — добавить `COMM_239149826` (группа `vk.com/tatar_stan_info`) через `/tokens` со scope `wall`. До этого каскадный дайджест собирается, но `wall.post` падает с `no publish-token available` (beat-слоты 9:45/19:45 MSK).
- ~~**Hot-fix: 3 hot-path'а парсинга не фильтровали disabled_until**~~ Закрыто 2026-05-27 ([PR #72](https://github.com/Valstan/setka/pull/72)). С момента блокировки VALSTAN ~10:00 все beat-таски брали первый токен из env (VALSTAN) и падали с VK error 5. Заменено на `get_active_parse_tokens(session)` в `tasks/parsing_scheduler_tasks.py:188-192`, `modules/cascaded_digest.py:310-313`, `modules/copy_setka_network.py:88-101` (убран опасный fallback на полный список). Подтверждено: после restart worker'а cascaded-таска собрала 55 постов вместо 0.
- 🟡 **wall.repost — единственная точка отказа на одном user-токене (VALSTAN).** Сейчас работает (VALSTAN перевыпущен 2026-05-28). На будущее, если VALSTAN снова умрёт: добавить второго user-токена в `VK_PUBLISH_TOKEN_NAMES` (например OLGA) — TokenPolicy подхватит как fallback. Либо переписать copy_setka на wall.post вместо wall.repost. Не срочно.
- 🟢 **UI чекбокс «использовать для публикаций»** прямо в `/tokens` — сейчас whitelist задаётся env (`VK_PUBLISH_TOKEN_NAMES`), хочется UI-override через `vk_tokens.role`.

### Прочее

- ~~**`logs/app.log` не пишется с 2026-05-22**~~ Закрыто 2026-05-25: разбор показал, что app.log не был «сломан» — `FileHandler` работал, файл был открыт, но `LOG_LEVEL=WARNING` в `/etc/setka/setka.env` отсекал 99% событий, а WARNING'ов с тех пор просто не было (`metrics_middleware` slow-request threshold 1.0s, а `/api/health/full` стабильно отдаёт ~1.01s). Параллельно содержимое app.log 100% дублировалось в `uvicorn_production.log` через systemd `StandardOutput/Error=append:`. Решение — убрать FileHandler из `main.py` полностью, оставить единственный канал через stderr → systemd-редирект → `uvicorn_production.log`. Дефолт `LOG_LEVEL` поднят с `WARNING` до `INFO`. На проде убран `LOG_LEVEL=WARNING` из `setka.env`, старый `app.log` архивирован. Doc-ссылки на `app.log` обновлены во всех `.md` / `.claude/commands/`.

### Git / brain_matrica integration

- ~~**Branch protection rules на GitHub для `main`.**~~ Закрыто 2026-05-23 (см. `DEV_HISTORY.md`): PR required (0 approvals) + CI `test (3.12)` required + strict + no force push + no deletion + enforce_admins. Конфиг в `scripts/branch-protection.json`. Hot-fix runbook — в `docs/OPERATIONS.md` §8.

### Прод-доступ

- ~~**SSH alias `setka-prod` vs `setka`.**~~ Закрыто 2026-05-23 (см. `DEV_HISTORY.md`): sweep по 13 файлам — `CLAUDE.md`, `.claude/settings.json`, `.claude/commands/{start,check,celery,logs,sql,reliz,finish}.md`, `.gitignore`, `database/migrations/README.md`, `scripts/migrate.py` — везде `setka-prod` → `setka`. `docs/DEV_HISTORY.md` и закрытые техдолги в этом файле не тронуты (исторические записи).

### Запуск и окружение

- ~~**`main.py:25` хардкодит `/home/valstan/SETKA/logs/app.log`.**~~ Закрыто ранее: `main.py:45` уже использует `os.getenv("LOG_PATH", "/home/valstan/SETKA/logs/app.log")` с safe-fallback на StreamHandler. Запись была устаревшей.
- ~~**`venv` создаётся вручную в каждом worktree.**~~ Закрыто ранее: есть `scripts/setup-dev.ps1` и `scripts/setup-dev.sh`. 2026-05-22 добавлено `pre-commit install` в оба скрипта — теперь свежий worktree сразу получает git-хук.
- ~~**`scripts/setup-dev.ps1` хардкодит `py -3.11`**~~ Закрыто 2026-05-23 (см. `DEV_HISTORY.md`): fallback `py -3.11 → -3.12 → -3` по аналогии с `setup-dev.sh`. Заодно добавлен UTF-8 BOM (PS 5.1 без BOM путал encoding на русской локали).
- ~~**Хардкоды `/home/valstan/SETKA/logs/parser*` в `web/api/parsing.py` и `tasks/parsing_tasks.py`.**~~ Закрыто 2026-05-23 (см. `DEV_HISTORY.md`): введён env `SETKA_LOGS_DIR` (default `/home/valstan/SETKA/logs`), `OUTPUT_DIR`/`REPORTS_DIR`/`VIDEO_REPORT_PATH` вычисляются от него, `_init_logger` safe-fallback'ит на StreamHandler при недоступном пути. В `web/api/parsing.py` удалён неиспользуемый дубль `OUTPUT_DIR`. +5 тестов в `tests/test_tasks/`.

### Документация / разработка

- ~~**Шаблон записи `DEV_HISTORY.md`**~~ Закрыто ранее: шаблон в шапке `DEV_HISTORY.md` (раздел «Правила записи» + collapsible шаблон).
- ~~**Pre-commit и CI разъезжаются**~~ Закрыто ранее: `.pre-commit-config.yaml` фиксирует `default_language_version.python: python3.11` для всех хуков. Прод (3.12) и линтеры (3.11) дают одинаковый стиль.
- ~~**Доочистка legacy flake8-ошибок**~~ Закрыто 2026-05-23 за 3 PR (см. `DEV_HISTORY.md` «Legacy flake8 cleanup PR 1/2/3»):
  - **PR #17** — E712 (47), F841 (18), W291 (16), E722 (2), F601 (2 + **реальный баг** в фильтре рекламы), F811 (2 + **реальный баг** в `/api/system_monitoring/live`) — всего ~88 правок и 2 найденных runtime-бага.
  - **PR #18** — E501 (96 строк) → `# noqa: E501`.
  - **PR #19** — E402 (147 импортов) → `# noqa: E402`.
  - `extend-ignore` обрезан с `E203,W503,E402,E501,E712,F841,W291,E303,E722,F601,F811,E302,W391,F541` (14 кодов) до `E203,W503` (только black ↔ pep8 конфликт). Все новые нарушения flake8 теперь падают в pre-commit.
- ~~**Отслеживать F601-фикс в фильтре рекламы**~~ Закрыто 2026-05-26: ratio стабилизировался. Замеры за 3 окна (0.347 % → 0.600 % → **0.54 %** за последние 100 succeeded-tasks 2026-05-25 21:21 → 2026-05-26). Колеблется в коридоре 0.35-0.60 %, далеко от тревожного порога 1.5 %. PR [#35](https://github.com/Valstan/setka/pull/35) фиксировал нитку ещё 2026-05-24, эта запись была вторичным мониторингом. Решение оставить вес price-patterns=2.
- ~~**Инкрементально ломать длинные строки, помеченные `# noqa: E501`**~~ Закрыто 2026-05-24 (см. `DEV_HISTORY.md` «Break long lines PR #1-#4»). За 4 атомарных PR в один день: PR #1 (`modules/system_status_notifier.py`, 15), PR #2 (`tasks/parsing_tasks.py`, 10), PR #3 (`tasks/vk_carousel_tasks.py` + `modules/service_activity_notifier.py`, 8), PR #4 (остальные 40 файлов, 63). **В проекте 0 строк с `# noqa: E501`**. Поведение функций не менялось (Python склеивает adjacent string literals на этапе компиляции).
- ~~**Рефакторинг `scripts/*` через `pyproject.toml` + `pip install -e .`**~~ Закрыто 2026-05-24 (см. `DEV_HISTORY.md` «pyproject.toml + editable install»). Создан `pyproject.toml`, добавлен `pip install -e .` в CI и `scripts/setup-dev.{sh,ps1}`. Из 53 файлов удалён `sys.path.insert(0, ...)` и ~50 связанных unused-импортов; ~90 `# noqa: E402` снято. Осталось ~30 legit-кейсов noqa: E402 (logging.basicConfig / os.environ.setdefault до импортов в scripts/tests). **Прод-action item**: разовый `ssh setka 'cd /home/valstan/SETKA && ./venv/bin/pip install -e .'` после merge.
- ~~**Покрыть тестами восстановленные F821-ветки**~~ Закрыто 2026-05-23 (см. `DEV_HISTORY.md`): +14 тестов в `tests/test_core/test_context_factory.py` (3), `tests/test_utils/test_retry_utility.py` (6), `tests/test_utils/test_text_utils.py` (5). Покрыты `ContextFactory.create_from_region`, `retry_with_fallback`, `retry_with_circuit_breaker` (+`CircuitBreaker` сценарии заодно), `truncate_text` (+ integration через `TextOnlyDigestBuilder.build_bezfoto_digest`). Итого 379/379 зелёных.

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
- **Скрипт `scripts/dev-doctor.sh`** проверяет окружение: Python 3.11/3.12, venv, requirements, postgresql-клиент, ssh alias `setka`, доступ к проду.
- **Hook на `git commit`**, который проверяет качество commit message (Conventional Commits префикс + наличие тела для `feat`/`fix`/`refactor`) — теперь когда DEV_HISTORY упразднена, commit message несёт всю историю. Через `.git/hooks/commit-msg` или husky-аналог для Python.
- **Smoke-test после деплоя** — отдельный шаг в `/reliz`: парс одного тестового региона/темы в test-режиме без публикации (`scripts/test_parse_run.py`) и сравнение вывода с baseline.

### Наблюдаемость

- ~~**Cross-process rate-limit на VKClient**~~ Закрыто 2026-05-26: `modules/vk_monitor/rate_limiter.py` с двумя backend'ами (ThreadingRateLimiter default, RedisRateLimiter через Lua-script с PEXPIRE). Selection через env `VK_RATE_LIMIT_BACKEND=redis|threading`. Graceful fallback на threading при недоступном Redis. +8 тестов.
- ~~**Дашборд «состояние дайджестов»**~~ Закрыто 2026-05-26: Prometheus + Grafana стек, дашборд `SETKA — состояние дайджестов` (4 панели: heatmap часов с публикации, stat-плашка простаивающих регионов, темп публикаций, pie долей по темам). Метрики: `setka_digest_published_total{region,topic,result}` + `setka_digest_last_published_timestamp{region,topic}`. Установка: `scripts/setup-monitoring.sh`. Доступ через SSH tunnel. См. `monitoring/README.md`.
- ~~**Multiprocess metrics для worker'а**~~ Закрыто 2026-05-26: `track_digest_published` вызывается из Celery worker'а, а `/metrics` живёт в web — без shared backend счётчики из worker'а до Prometheus не доходят (дашборд оставался пустым). Поднят `PROMETHEUS_MULTIPROC_DIR=/var/lib/setka/prom_multiproc` + `MultiProcessCollector` в `monitoring/metrics.py`; `digest_last_published_timestamp` Gauge получил `multiprocess_mode='max'`, остальные — `'livesum'`. `setup-monitoring.sh` создаёт каталог + drop-in `setka.service.d/prometheus-multiproc.conf` (то же для celery-worker). Celery worker_shutdown hook вызывает `mark_process_dead(pid)`. +4 теста.
- ~~**`setka_digest_published_total` остаётся пуст несмотря на успешные публикации.**~~ Закрыто 2026-05-28 ([PR #75](https://github.com/Valstan/setka/pull/75)): убран `ExecStartPre=/bin/rm -rf $PROM_MULTIPROC_DIR` из шаблона drop-in (`scripts/setup-monitoring.sh`) + из обоих прод-drop-in'ов `/etc/systemd/system/setka{,-celery-worker}.service.d/prometheus-multiproc.conf` + daemon-reload + restart. Каталог общий — `rm -rf` при рестарте любого сервиса сносил mmap другого. Очистку stale-PID делает `mark_process_dead` в worker_shutdown hook. Прод-проверка: файлы метрик пережили рестарт. `gauge_max_*.db` появится после первой реальной публикации. _Исходная диагностика 2026-05-26/27 ниже._
  <details><summary>Исходная диагностика</summary>Обнаружено 2026-05-26 сразу после релиза multiproc-фикса; **обновлено 2026-05-27** после расследования в smoke-сессии. Прямой smoke-test на проде (`./venv/bin/python -c "from monitoring.metrics import track_digest_published; track_digest_published(...)"`) **работает** — counter и Gauge инкрементируются, `gauge_max_*.db` создаётся. Cascaded-таска через celery worker (после hot-fix PR #72) тоже завершалась с `posts_published > 0` и явным `pub.success=False` (VK error 10) → код-path с `track_digest_published(result="failed")` точно выполнялся. Но в `/var/lib/setka/prom_multiproc/` после рестарта worker'а есть только `counter_<worker_pid>.db` + `gauge_livesum_<worker_pid>.db`; **нет ни одного `gauge_max_*.db`** (для `digest_last_published_timestamp`, mode='max'), и `curl /metrics | grep setka_digest_` пусто. **Корневая гипотеза**: drop-in для `setka-celery-worker.service` имеет `ExecStartPre=/bin/rm -rf /var/lib/setka/prom_multiproc` (создан `scripts/setup-monitoring.sh`), который **сносит весь каталог при каждом restart worker'а** — включая файлы web-процесса. Поскольку оба сервиса делят один и тот же каталог через тот же drop-in, любой restart обнуляет состояние. Фикс: убрать `rm -rf` из drop-in (он создавался для очистки stale-файлов, но `mark_process_dead(pid)` в worker_shutdown hook уже эту работу делает корректно). **Файлы**: `scripts/setup-monitoring.sh:?`, `/etc/systemd/system/setka.service.d/prometheus-multiproc.conf`, `/etc/systemd/system/setka-celery-worker.service.d/prometheus-multiproc.conf`. Старые ссылки на `modules/kirov_oblast_digest.py:438,487` устарели — после PR #70 трекинг в `modules/cascaded_digest.py:454+` (block при `pub.success`).</details>
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
- **Discovery — расширенные источники кандидатов для существующих регионов.** Сейчас при ручном re-discovery (кнопка «🔍 Найти новые сообщества») и beat-таске `discovery-rolling-daily` используются те же source'ы что и при создании региона: `groups.search` по localities + keywords + info-repost ([PR #51](https://github.com/Valstan/setka/pull/51)). Идеи новых источников: **(a)** подписки/`groups.get` админов уже-добавленных сообществ (часто люди подписаны на тематически близкие группы); **(b)** `members.get` главной ИНФО-страницы → `users.getSubscriptions` для top-N активных подписчиков (фильтруя fake-аккаунты по member_count); **(c)** `wall.search` по localities в окне 30 дней — ловит свежие публикации, которые `groups.search` пропустил; **(d)** парсинг hashtag'ов и `@-mentions` из существующих постов главной ИНФО-страницы — там часто упоминаются районные блоги. Все источники → в `community_candidates` с `source` колонкой (уже есть после #51) и pending-статусом для модерации.
- **Discovery — фоновый «watcher» репостов главной ИНФО-страницы.** Расширение PR #51: вместо ad-hoc вычитки при manual trigger, beat-таска раз в N часов сканирует последние посты главной ИНФО-страницы каждого активного региона, извлекает `copy_history.owner_id`, добавляет неизвестные группы в `community_candidates(source='info-repost-watch', status='pending')`. Эффект: за неделю автоматически набираются «дружественные» группы которые уже репостят друг друга. Дёшево по VK-квоте.
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
