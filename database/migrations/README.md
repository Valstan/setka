# Database migrations — правила и применение

## Применение

Предпочтительно через `scripts/migrate.py` (см. ниже). Если нужно
накатить одну конкретную миграцию руками, по-прежнему работает:

```bash
ssh setka-prod 'sudo -u postgres psql -d setka -f /home/valstan/SETKA/database/migrations/NNN_*.sql'
```

Также можно через `/sql` slash-команду в Claude-сессии (она спросит
подтверждение перед DML).

После 009 (`ALTER DEFAULT PRIVILEGES`) `setka_user` автоматически
получает GRANT на любые новые таблицы под postgres — explicit `GRANT`
в новых миграциях больше не нужен.

## Runner `scripts/migrate.py`

С миграции 010 учётом применённых занимается таблица
`applied_migrations` (filename, sha256, applied_at). Скрипт
`scripts/migrate.py` (stdlib-only) сверяет содержимое каталога с этой
таблицей и применяет недостающее по порядку (сортировка по имени файла).

```bash
# на прод-VPS
ssh setka-prod 'cd /home/valstan/SETKA && python3 scripts/migrate.py status'
ssh setka-prod 'cd /home/valstan/SETKA && python3 scripts/migrate.py up --dry-run'
ssh setka-prod 'cd /home/valstan/SETKA && python3 scripts/migrate.py up'
```

Каждая миграция применяется в одной транзакции вместе с
INSERT-ом в `applied_migrations` под `sudo -u postgres psql -v
ON_ERROR_STOP=1`. Если SQL упал — транзакция откатывается, запись о
применении не появляется. После правки уже применённой миграции
`up` поправит её sha256 в таблице (`ON CONFLICT DO UPDATE`).

010 — bootstrap-миграция: при первом `up` runner видит, что таблицы
ещё нет, и применяет 010 первой. Backfill внутри 010 фиксирует 003-009
+ `add_sentiment_fields.sql` как «уже применены» с пустым sha256.

## Правила для новых миграций

**Все миграции в этом каталоге обязаны быть идемпотентными.** Это значит,
что повторное применение не должно падать с ошибкой и не должно менять
состояние БД иначе чем первое применение. Зачем — два кейса:
1. Восстановление из `pg_dump`, который уже включает схему — после rest'а
   нужно применить миграции, появившиеся **после** дампа, и желательно
   все подряд, без угадывания.
2. Свежая dev-БД из `git clone` — `psql -f *.sql` подряд должен
   развернуть текущее состояние.

### Идемпотентные конструкции PostgreSQL

| Хочешь | Используй |
|---|---|
| создать таблицу | `CREATE TABLE IF NOT EXISTS ...` |
| создать индекс | `CREATE INDEX IF NOT EXISTS ...` |
| создать sequence | `CREATE SEQUENCE IF NOT EXISTS ...` |
| добавить колонку | `ALTER TABLE ... ADD COLUMN IF NOT EXISTS ...` |
| удалить колонку | `ALTER TABLE ... DROP COLUMN IF EXISTS ...` |
| создать или заменить функцию | `CREATE OR REPLACE FUNCTION ...` |
| создать триггер (PG < 15) | `DROP TRIGGER IF EXISTS ... ON ...; CREATE TRIGGER ...` |
| создать или заменить триггер (PG ≥ 15) | `CREATE OR REPLACE TRIGGER ...` |
| вставить «эталонную» строку | `INSERT ... ON CONFLICT (...) DO NOTHING` |
| обновить существующее | `UPDATE ... WHERE <условие, что ещё не сделано>` |
| выдать права | `GRANT ... TO ...` (всегда идемпотентно) |
| снять права | `REVOKE ... FROM ...` |
| настроить дефолтные права | `ALTER DEFAULT PRIVILEGES ... GRANT ...` (всегда идемпотентно) |

### Что **нельзя** делать в миграциях

- `CREATE TABLE ...` без `IF NOT EXISTS`
- `CREATE INDEX ...` без `IF NOT EXISTS`
- `INSERT INTO ... VALUES (...)` без `ON CONFLICT DO NOTHING`
- `ALTER TABLE ... ADD COLUMN ...` без `IF NOT EXISTS`
- `CREATE TRIGGER ...` без обёртки в `DROP TRIGGER IF EXISTS`

### Нумерация

- `NNN_<краткое_описание>.sql`, где `NNN` — три цифры, монотонно
  растущие (003, 004, …). Следующая миграция — `010_*.sql`.
- `add_sentiment_fields.sql` (без номера) — legacy, применена давно,
  не переименовываем чтобы не путать `git log`.

### Сборка дампа

Если применяешь миграцию, которая что-то меняет в существующих данных
(`UPDATE` / data migration) — оставь в комментарии `-- WAS APPLIED <YYYY-MM-DD>`
после первого применения на проде. Это поможет понять «когда эта строка
данных получила своё текущее состояние».

## Применённые миграции (на 2026-05-22)

| Файл | Что делает |
|---|---|
| `003_vk_tokens.sql` | Таблица `vk_tokens` + триггер `updated_at` |
| `004_update_vk_tokens.sql` | Добавляет колонки в `vk_tokens` |
| `005_region_configs_digest_filters.sql` | `region_configs.digest_filters JSONB` |
| `006_region_configs_localities.sql` | `region_configs.localities JSONB` |
| `007_vk_tokens_community_id.sql` | `vk_tokens.community_id` + частичный индекс |
| `008_message_templates.sql` | Таблица `message_templates` (этап 4b) |
| `009_alter_default_privileges.sql` | `ALTER DEFAULT PRIVILEGES` для `setka_user` |
| `010_applied_migrations.sql` | Bookkeeping-таблица для `scripts/migrate.py` |
| `add_sentiment_fields.sql` | `posts.sentiment_*` (legacy, без номера) |

При добавлении новой миграции — обнови эту таблицу.
