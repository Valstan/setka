# Прод-операции

> Запись `P008` реестра [PENDING_FOLLOWUPS](../PENDING_FOLLOWUPS.md). Индекс грепается, запись читается целиком, реестр целиком не читается никогда ([D-097](../../AGENTS.md)).

- ~~**Применение SQL-миграций — ручное.**~~ Закрыто 2026-05-22: миграция 010 + `scripts/migrate.py` (stdlib, через `sudo -u postgres psql`). Сверяется с `applied_migrations`, применяет недостающее в транзакции вместе с INSERT-ом. Использование: `ssh sarafan-prod 'cd ~/SETKA && python3 scripts/migrate.py up'`.
- ~~**GRANT в миграциях / ALTER DEFAULT PRIVILEGES.**~~ Закрыто 2026-05-22 миграцией 009 (см. `DEV_HISTORY.md`). Будущие миграции не должны включать explicit `GRANT ALL ... TO setka_user` — default privileges выдаст их автоматически.
- ~~**Auto-mode classifier блокирует SSH на прод.**~~ Закрыто 2026-05-22: `.claude/settings.json` с `permissions.allow: ["Bash(ssh sarafan-prod:*)"]` (закоммичен в репо через `!.claude/settings.json` в `.gitignore`). Destructive-операции по-прежнему через `AskUserQuestion` — это политика CLAUDE.md, не permissions.

---
