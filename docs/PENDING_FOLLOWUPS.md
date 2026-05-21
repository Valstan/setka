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

Этапы 0, 1, 2 + hot-fix-2 закрыты 2026-05-21 (см. [`DEV_HISTORY.md`](DEV_HISTORY.md)):
- 0 — Fallback на user-token при VK error 15/27 + `keep_if_empty` в storage.
- 1 — Полный сбор комментариев: пагинация по offset, thread.items, `max_total_comments` 300→5000 safety cap.
- 2 — BaseVKChecker (DRY для fallback/cache), удалён UnifiedNotificationsChecker и dead-code `tasks/notification_tasks.py`, окно 8-22 только в crontab.
- hot-fix-2 — VKClient.api_call propagates error_code + regex fallback в `_invoke` для legacy-формата.

Дальше план:

- **Этап 3 — Storage с историей.** Redis-list `setka:notifications:history:{type}` (LPUSH+LTRIM до 24 записей, TTL 25ч). API `GET /history` и `GET /stats`. UI-виджет «активность за сутки» (Chart.js).
- **Этап 4a — UI обратной связи (часть 1).** Inline-ответ из SETKA (`wall.createComment`); **лайк коммента от имени сообщества** (`likes.add` через community-token, кнопка-сердечко); mark-as-handled / архив (Redis `setka:notifications:handled:{id}` TTL 7 дней); виджет «Горячие посты» (топ-5 за сутки с >10 комментариев).
- **Этап 4b — UI обратной связи (часть 2).** **Шаблонные ответы на сообщения** (`messages.send` через community-token + отдельный экран `/templates` для CRUD шаблонов); **AI-черновик** через Groq (кнопка «Сгенерировать ответ» → редактируемая textarea); Telegram-бот inline-кнопка «Ответить из SETKA».
- **Этап 5 — Мониторинг.** Prometheus метрика `notifications_check_total{type,result}`. Алёрт в Telegram «3 автопроверки подряд возвращают error 27 — токены сломаны».

---

## 🟡 Техдолги

### Запуск и окружение

- **`main.py:25` хардкодит `/home/valstan/SETKA/logs/app.log`.** Локально на Windows запустить приложение нельзя — только тесты. Стоит вынести путь в env (`LOG_PATH`) с дефолтом на прод-значение.
- **`venv` создаётся вручную в каждом worktree.** Нет скрипта `scripts/setup-dev.ps1` / `scripts/setup-dev.sh`, который бы делал `py -3.11 -m venv venv && .\venv\Scripts\pip install -r requirements.txt pytest pytest-asyncio`. Сейчас это память + ручная работа.

### Документация / разработка

- **Шаблон записи `DEV_HISTORY.md`** — нет явного шаблона в файле. Стоит добавить «как писать новую запись» в его шапку (формат заголовка, секции, ссылки на тесты).
- **Pre-commit и CI разъезжаются** — pre-commit зашит на `python3.12`, прод тоже 3.12, но локально для тестов 3.11. Иногда black/isort даёт разные стили между 3.11 и 3.12. Стоит зафиксировать одну версию для pre-commit (опционально через `pre-commit-config.yaml → language_version: python3.11`).

### Прод-операции

- **Применение SQL-миграций — ручное.** В `database/migrations/*.sql` есть нумерация (001, 002, ...), но нет таблицы `applied_migrations` и нет идемпотентности (часть миграций можно прогонять повторно через `IF NOT EXISTS`, часть нет). Стоит либо ввести `applied_migrations`-учёт (как у Payload в Гоньбе), либо привести все миграции к идемпотентному виду.
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

- **Дашборд «состояние дайджестов»** — Grafana-панель: «когда последний раз публиковал регион X тему Y», на основе Redis-ключей `setka:digest_last_published:*` и логов `celery-worker.log`.
- **Алёрт в Telegram-бот**, если за последние 6 часов ни один регион не выпустил `novost`-дайджест (=что-то сломалось в beat).
- **Структурированные логи** — Celery worker сейчас пишет plain-text. Переход на JSON-логи + `journalctl -u setka-celery-worker -o json` + Loki/Prometheus упростит разбор инцидентов.

### Продукт

- **UI «История публикаций» по регионам и темам** — `web/templates/publications.html`? Сейчас контроль идёт через VK-стены, нет своего удобного просмотра.
- **«Тёмный режим» для UI** — `/regions`, `/posts`, `/filtration` — длинные таблицы, ночью глаза вытекают.
- **`/regions/<code>/diagnostics`** — кнопка «прогнать пайплайн без публикации» в UI: видно, что отфильтровалось, что собрал aggregator, что попало бы в дайджест.

---

## История пересечений

Если задача висела долго и пересекалась с несколькими сессиями — пиши тут историю переноса дат, чтобы было видно, что она «застряла».

_Сейчас пусто._
