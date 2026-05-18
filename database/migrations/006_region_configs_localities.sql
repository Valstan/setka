-- Населённые пункты района для региональных фильтров
-- Применять на проде:
--   psql $DATABASE_URL -f database/migrations/006_region_configs_localities.sql

ALTER TABLE region_configs
    ADD COLUMN IF NOT EXISTS localities JSONB DEFAULT NULL;

COMMENT ON COLUMN region_configs.localities IS
    'JSON-список населённых пунктов района (строки). Используется RegionalRelevanceFilter '
    'вместе с region_words для проверки региональной релевантности постов. '
    'Пример: ["Цепочкино", "Гоньба", "Калинино"].';
