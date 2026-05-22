-- 010: applied_migrations — учёт применённых миграций для scripts/migrate.py
--
-- Зачем: до 010 миграции применялись вручную и нигде не фиксировались.
-- При восстановлении из pg_dump или развёртывании на свежий dev-инстанс
-- было непонятно, что уже накатано, а что — нет. Теперь runner
-- (`scripts/migrate.py up`) сам сверяется с этой таблицей.
--
-- Связка с runner-ом: при каждом успешном применении миграции внутри
-- одной транзакции делается INSERT в эту таблицу (filename, sha256).
-- sha256 — контрольная сумма содержимого .sql на момент применения,
-- помогает поймать «миграцию подправили после применения».
--
-- Backfill ниже фиксирует уже-применённые на 2026-05-22 миграции —
-- runner на первом запуске не будет пытаться накатить их заново.
-- sha256 для backfill оставлен пустой строкой: при отсутствии sha256
-- runner считает миграцию применённой, но не сверяет хеш. На свежем
-- pg_dump после 010 такого не будет — все хеши пишутся честно.
--
-- Идемпотентна.

CREATE TABLE IF NOT EXISTS applied_migrations (
    id SERIAL PRIMARY KEY,
    filename VARCHAR(200) NOT NULL UNIQUE,
    sha256 VARCHAR(64) NOT NULL,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_applied_migrations_applied_at
    ON applied_migrations(applied_at);

INSERT INTO applied_migrations (filename, sha256) VALUES
    ('003_vk_tokens.sql', ''),
    ('004_update_vk_tokens.sql', ''),
    ('005_region_configs_digest_filters.sql', ''),
    ('006_region_configs_localities.sql', ''),
    ('007_vk_tokens_community_id.sql', ''),
    ('008_message_templates.sql', ''),
    ('009_alter_default_privileges.sql', ''),
    ('add_sentiment_fields.sql', '')
ON CONFLICT (filename) DO NOTHING;
