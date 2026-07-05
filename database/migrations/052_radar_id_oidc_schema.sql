-- 052: Радар-ID Ф1 — схема OIDC-провайдера (ADR-0002, контракт ратифицирован
-- brain 2026-06-30, from-brain/2026-06-30-radar-sso-contract-ratified.md).
--
-- 1) radar_users становится каноническим аккаунт-слоем экосистемы:
--    - sub: opaque OIDC subject (UUID, не serial PK — не светим счётчик);
--    - email/email_verified: verified-флаг критичен для безопасного
--      связывания соц-личности с существующим аккаунтом;
--    - display_name + связанные upstream-id (vk/telegram/yandex);
--    - login/password_hash становятся nullable (соц-only аккаунты).
-- 2) Три oauth-таблицы: клиенты (ручная регистрация), одноразовые
--    authorization codes, refresh-токены с family-based reuse-detection.
--
-- Аддитивно и идемпотентно. Откат:
--   DROP TABLE IF EXISTS oauth_refresh_tokens, oauth_auth_codes, oauth_clients;
--   ALTER TABLE radar_users
--     DROP COLUMN IF EXISTS sub, DROP COLUMN IF EXISTS email,
--     DROP COLUMN IF EXISTS email_verified, DROP COLUMN IF EXISTS display_name,
--     DROP COLUMN IF EXISTS vk_user_id, DROP COLUMN IF EXISTS telegram_user_id,
--     DROP COLUMN IF EXISTS yandex_id;
--   (login/password_hash NOT NULL не восстанавливаем автоматически — могли
--    появиться соц-only строки.)

-- ── radar_users: аккаунт-слой ──

ALTER TABLE radar_users ADD COLUMN IF NOT EXISTS sub UUID NULL;
ALTER TABLE radar_users ADD COLUMN IF NOT EXISTS email VARCHAR(255) NULL;
ALTER TABLE radar_users ADD COLUMN IF NOT EXISTS email_verified BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE radar_users ADD COLUMN IF NOT EXISTS display_name VARCHAR(128) NULL;
ALTER TABLE radar_users ADD COLUMN IF NOT EXISTS vk_user_id BIGINT NULL;
ALTER TABLE radar_users ADD COLUMN IF NOT EXISTS telegram_user_id BIGINT NULL;
ALTER TABLE radar_users ADD COLUMN IF NOT EXISTS yandex_id VARCHAR(64) NULL;

-- Backfill sub для существующих аккаунтов (gen_random_uuid: PG13+ builtin),
-- затем NOT NULL.
UPDATE radar_users SET sub = gen_random_uuid() WHERE sub IS NULL;
ALTER TABLE radar_users ALTER COLUMN sub SET NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_radar_users_sub ON radar_users (sub);
-- email уникален без учёта регистра (вместо citext — без внешних extension).
CREATE UNIQUE INDEX IF NOT EXISTS uq_radar_users_email_lower
    ON radar_users (lower(email)) WHERE email IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_radar_users_vk_user_id
    ON radar_users (vk_user_id) WHERE vk_user_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_radar_users_telegram_user_id
    ON radar_users (telegram_user_id) WHERE telegram_user_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_radar_users_yandex_id
    ON radar_users (yandex_id) WHERE yandex_id IS NOT NULL;

-- Соц-only аккаунты: пароль и login могут отсутствовать.
ALTER TABLE radar_users ALTER COLUMN password_hash DROP NOT NULL;
ALTER TABLE radar_users ALTER COLUMN login DROP NOT NULL;

-- ── oauth_clients: ручная регистрация клиентов (ADR-0002 §8) ──

CREATE TABLE IF NOT EXISTS oauth_clients (
    id SERIAL PRIMARY KEY,
    client_id VARCHAR(64) NOT NULL UNIQUE,
    -- NULL для public PKCE-only клиентов (мобайл Ф3); confidential хранят hash.
    client_secret_hash VARCHAR(256) NULL,
    name VARCHAR(128) NOT NULL,
    -- JSON-массив точных redirect_uri (символ-в-символ, punycode для .рф — G108).
    redirect_uris JSONB NOT NULL DEFAULT '[]',
    -- Space-separated allowed scopes: клиент физически не получит больше.
    allowed_scopes VARCHAR(255) NOT NULL DEFAULT 'openid',
    is_confidential BOOLEAN NOT NULL DEFAULT TRUE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- ── oauth_auth_codes: single-use authorization codes ──

CREATE TABLE IF NOT EXISTS oauth_auth_codes (
    id BIGSERIAL PRIMARY KEY,
    -- Храним sha256(code), не сырой код.
    code_hash VARCHAR(128) NOT NULL UNIQUE,
    client_id VARCHAR(64) NOT NULL,
    user_id INTEGER NOT NULL REFERENCES radar_users(id) ON DELETE CASCADE,
    redirect_uri TEXT NOT NULL,
    scope VARCHAR(255) NOT NULL,
    code_challenge VARCHAR(128) NULL,
    code_challenge_method VARCHAR(10) NULL,
    nonce VARCHAR(255) NULL,
    auth_time TIMESTAMP NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    used_at TIMESTAMP NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_oauth_auth_codes_expires_at ON oauth_auth_codes (expires_at);

-- ── oauth_refresh_tokens: ротация + family reuse-detection (MUST §5.2) ──

CREATE TABLE IF NOT EXISTS oauth_refresh_tokens (
    id BIGSERIAL PRIMARY KEY,
    token_hash VARCHAR(128) NOT NULL UNIQUE,
    -- Все ротации одного логина делят family_id; reuse погашенного токена
    -- → отзыв всей family.
    family_id UUID NOT NULL,
    user_id INTEGER NOT NULL REFERENCES radar_users(id) ON DELETE CASCADE,
    client_id VARCHAR(64) NOT NULL,
    scope VARCHAR(255) NOT NULL,
    rotated_from BIGINT NULL,
    revoked_at TIMESTAMP NULL,
    expires_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_oauth_refresh_tokens_family_id ON oauth_refresh_tokens (family_id);
CREATE INDEX IF NOT EXISTS ix_oauth_refresh_tokens_user_client
    ON oauth_refresh_tokens (user_id, client_id);
