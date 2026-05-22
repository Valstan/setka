-- 009: ALTER DEFAULT PRIVILEGES so setka_user auto-inherits rights on
-- everything created in schema `public` by postgres.
--
-- Контекст: 2026-05-22 миграция 008 создала таблицу `message_templates`
-- из-под `sudo -u postgres psql -d setka -f ...` (owner=postgres), и
-- application-user `setka_user` не унаследовал GRANT'ы автоматически —
-- /api/templates/ ответил 500 «InsufficientPrivilegeError». Hot-fix
-- (см. DEV_HISTORY 2026-05-22) — ручной GRANT + GRANT в 008.
--
-- Эта миграция делает ситуацию правильной раз и навсегда: настраивает
-- default privileges, чтобы любой будущий CREATE TABLE / CREATE
-- SEQUENCE под postgres сразу был доступен setka_user. Заодно
-- выравнивает уже-существующие таблицы и sequence'ы (там, где GRANT
-- мог не быть выдан).
--
-- Идемпотентна: повторное применение — no-op, существующих данных
-- не меняет, только privileges.

-- 1. Базовый USAGE на схему public (нужен для дальнейших GRANT'ов на
-- таблицы внутри неё; на проде уже выдан, но дублируем для свежей
-- базы restore'd из дампа).
GRANT USAGE ON SCHEMA public TO setka_user;

-- 2. Выровнять права на все существующие таблицы и sequence'ы.
-- Это страховка: после 009 НИ ОДНА из текущих таблиц не должна давать
-- InsufficientPrivilegeError, даже если её владелец — postgres.
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO setka_user;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO setka_user;

-- 3. Default privileges для будущих объектов.
-- FOR ROLE postgres — потому что миграции применяются под postgres.
-- Для объектов, созданных под setka_user (как `parsing_stats`, `region_configs`)
-- default privileges не нужны — owner и так имеет полный доступ.
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
    GRANT ALL ON TABLES TO setka_user;

ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO setka_user;
