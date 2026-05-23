---
description: Безопасное выполнение SQL на прод-БД SETKA через SSH с обязательным подтверждением для DML.
argument-hint: <SQL запрос | shortcut: describe <table> | tables | count <table> | migrate <NNN_file.sql>>
allowed-tools: Bash, Read, Glob, AskUserQuestion
---

# /sql — выполнить SQL на прод-БД SETKA

**ВСЕГДА** показывает пользователю что собирается сделать и спрашивает подтверждение через `AskUserQuestion` перед любым `INSERT/UPDATE/DELETE/ALTER/DROP/TRUNCATE/CREATE`.

`SELECT/EXPLAIN/\d/\dt/SHOW` — read-only, можно без подтверждения.

## Параметры подключения

```
ssh setka 'sudo -u postgres psql -d setka -c "<SQL>"'
```

(на проде sudo без пароля, БД `setka`, пользователь приложения — `setka_user`, для админ-запросов — `postgres`).

## Логика

### 1. Распознать тип запроса из `$ARGUMENTS`

- **Шорткаты** (раскрываются перед классификацией):
  - `tables` → `\dt`
  - `describe <table>` → `\d <table>`
  - `count <table>` → `SELECT count(*) FROM <table>`
  - `migrate <NNN_file.sql>` → особый flow, см. ниже
- **Read-only:** только `SELECT`, `EXPLAIN`, `\d`, `\dt`, `SHOW` → выполнить сразу.
- **Mutating:** содержит `INSERT|UPDATE|DELETE|ALTER|DROP|TRUNCATE|CREATE|GRANT|REVOKE` → обязательное подтверждение.

### 2. Перед mutating-запросом

- Показать **точный SQL** в блоке кода.
- Дополнительные проверки:
  - `UPDATE` / `DELETE` без `WHERE` → **отказ** с предупреждением.
  - `DROP TABLE` / `TRUNCATE` → **двойное подтверждение** (два последовательных AskUserQuestion).
  - `ALTER TABLE` на больших таблицах (`posts`, `work_tables`) — отдельно спросить «применять с `CONCURRENTLY` / в транзакции / без?».
- `AskUserQuestion`: «Применить этот SQL на прод-БД SETKA?» с опциями:
  - «Да, применяй»
  - «Сделай dry-run сначала» — обернуть в `BEGIN; ...; ROLLBACK;` и показать `EXPLAIN` или `WITH ... AS ... SELECT count(*)` для оценки.
  - «Отмена» — выйти.

### 3. Выполнение

**Однострочный:**

```bash
ssh -o ConnectTimeout=20 setka 'sudo -u postgres psql -d setka -c "<SQL>"' 2>&1
```

**Многострочный/файловый:**

```bash
# Залить временный файл и применить
scp /tmp/setka-sql-$$.sql setka:/tmp/setka-apply.sql
ssh setka 'sudo -u postgres psql -d setka -f /tmp/setka-apply.sql && rm /tmp/setka-apply.sql' 2>&1
```

(или heredoc через ssh — на выбор).

### 4. После выполнения

- Показать вывод psql (`UPDATE N`, `ALTER TABLE`, и т.д.).
- **Напомнить пользователю** про возможные последствия:
  - Если правка задевает `region_configs.digest_filters` или `region_configs.localities` — UI кеширует, может потребоваться `systemctl restart setka`.
  - Если `ALTER TABLE ADD COLUMN` — добавить в `database/migrations/` соответствующий SQL и в `docs/PENDING_FOLLOWUPS.md` (если ещё не сделано).
  - Если `TRUNCATE work_tables.*` — следующий парсинг проигнорирует историю дедупликации, посты могут повториться.

## Особый flow: применение миграции

```
/sql migrate 006_region_configs_localities.sql
```

1. `Glob` `database/migrations/006_*.sql` — найти файл.
2. `Read` — показать содержимое.
3. `AskUserQuestion`: «Применить миграцию на прод?» с опциями:
   - «Да, применяй»
   - «Сначала dry-run в транзакции с ROLLBACK»
   - «Отмена»
4. При «да»:
   ```bash
   scp database/migrations/<file>.sql setka:/tmp/setka-migration.sql
   ssh setka 'sudo -u postgres psql -d setka -f /tmp/setka-migration.sql' 2>&1
   ssh setka 'rm /tmp/setka-migration.sql'
   ```
5. Напомнить добавить в `DEV_HISTORY.md` запись «Миграция NNN применена на прод <дата>».

## Безопасные дефолты

- **Backup перед опасной операцией:** для `DROP TABLE` / `TRUNCATE` / массового `UPDATE` без явной микро-области — предложить через `AskUserQuestion` сначала сделать pg_dump:
  ```bash
  ssh setka "sudo -u postgres pg_dump -Fc -t <table> setka > /tmp/setka-<table>-$(date +%Y%m%d-%H%M).dump"
  ```
- **Никогда не выполнять** mutating-запрос, который видит впервые, без явного «да».
- **Никогда не повторять** опасную команду после отказа пользователя.

## Локальная БД

Локальной БД для разработки нет (см. memory `reference-local-env`). Все SQL — только на проде. Если нужно «попробовать» — попроси пользователя запустить локальную PostgreSQL отдельно, или используй dry-run в транзакции на проде.
