# Прод-доступ

> Запись `P011` реестра [PENDING_FOLLOWUPS](../PENDING_FOLLOWUPS.md). Индекс грепается, запись читается целиком, реестр целиком не читается никогда ([D-097](../../AGENTS.md)).

- ~~**SSH alias `setka-prod` vs `setka`.**~~ Закрыто 2026-05-23 (см. `DEV_HISTORY.md`): sweep по 13 файлам — `CLAUDE.md`, `.claude/settings.json`, `.claude/commands/{start,check,celery,logs,sql,reliz,finish}.md`, `.gitignore`, `database/migrations/README.md`, `scripts/migrate.py` — везде `setka-prod` → `setka`. `docs/DEV_HISTORY.md` и закрытые техдолги в этом файле не тронуты (исторические записи).
