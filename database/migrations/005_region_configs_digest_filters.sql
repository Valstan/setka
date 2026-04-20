-- Настройки пайплайна дайджеста (возраст поста по темам и др.)
-- Выполнить на существующей БД PostgreSQL:
--   psql $DATABASE_URL -f database/migrations/005_region_configs_digest_filters.sql

ALTER TABLE region_configs
    ADD COLUMN IF NOT EXISTS digest_filters JSONB DEFAULT NULL;

COMMENT ON COLUMN region_configs.digest_filters IS 'JSON: {defaults: {...}, by_topic: {sport: {...}}} см. modules/digest_pipeline_settings.py';
