-- 059: gateway_keys — БД как единый источник API-ключей VK-шлюза (мандат brain 2026-07-12,
-- паттерн #072: single source в БД, env GATEWAY_KEY_<PROJECT> остаётся bootstrap-fallback).
-- Семантика как у vk_tokens (#336): env добавляет только имена, которых нет в БД;
-- выключенный в БД ключ env НЕ воскрешает.
-- Аддитивная, без backfill: существующие env-ключи продолжают работать как fallback,
-- перенос в БД — через scripts/issue_gateway_key.py --import-env.

CREATE TABLE IF NOT EXISTS gateway_keys (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(50) NOT NULL UNIQUE,   -- PROJECT_NAME_UPPER (KAZANSKAYA, GONBA...)
    secret      TEXT        NOT NULL,          -- API-ключ (plaintext, как vk_tokens.token; БД root-only)
    is_active   BOOLEAN     NOT NULL DEFAULT TRUE,
    note        TEXT,                          -- кто/зачем (заявка, письмо brain)
    created_at  TIMESTAMP   NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),
    rotated_at  TIMESTAMP                      -- последняя ротация секрета
);

CREATE INDEX IF NOT EXISTS ix_gateway_keys_name ON gateway_keys (name);
CREATE INDEX IF NOT EXISTS ix_gateway_keys_is_active ON gateway_keys (is_active);

-- Откат:
-- DROP TABLE IF EXISTS gateway_keys;
