-- VK Tokens Management Table
-- Хранение токенов VK API в базе данных для динамического управления

CREATE TABLE IF NOT EXISTS vk_tokens (
    id SERIAL PRIMARY KEY,
    name VARCHAR(50) NOT NULL UNIQUE,  -- VALSTAN, OLGA, VITA, etc.
    token TEXT NOT NULL,               -- VK API токен
    is_active BOOLEAN DEFAULT true,    -- Активен ли токен
    last_used TIMESTAMP,               -- Последнее использование
    last_validated TIMESTAMP,          -- Последняя валидация
    validation_status VARCHAR(20) DEFAULT 'unknown', -- valid, invalid, unknown
    error_message TEXT,                -- Сообщение об ошибке при валидации
    permissions JSONB,                 -- Права доступа токена
    user_info JSONB,                   -- Информация о пользователе
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Индексы для быстрого поиска
CREATE INDEX IF NOT EXISTS idx_vk_tokens_name ON vk_tokens(name);
CREATE INDEX IF NOT EXISTS idx_vk_tokens_active ON vk_tokens(is_active);
CREATE INDEX IF NOT EXISTS idx_vk_tokens_status ON vk_tokens(validation_status);

-- Триггер для обновления updated_at
CREATE OR REPLACE FUNCTION update_vk_tokens_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_vk_tokens_updated_at
    BEFORE UPDATE ON vk_tokens
    FOR EACH ROW
    EXECUTE FUNCTION update_vk_tokens_updated_at();

-- Вставить токены из конфигурации (если их еще нет)
INSERT INTO vk_tokens (name, token, is_active, validation_status) 
VALUES 
    ('VALSTAN', 'vk1.a.nv5IKyDlt15vjgcELAdi5c9mduzY9Wob160azxF_AOblv45fu-sgeDxgwsdM0BKWlemtdHaIj27ap6e2Nt-bQ5JVQAkdUplOV9uRi9Kqa3nZRCH-lkmpKrLt6o_garU9CPbZu9KZVD-iU2mQuknY68bZasL74X8TZ_R2zcLl_2Y3XmU1TFR3wsP4M6Xju9IN2Ygo3V_05Spe1_4mVN2roA', true, 'unknown'),
    ('OLGA', 'vk1.a.YB3vu9mP072pkadsec7VVBDaIjke_VByDUks3QnLaWsbbu28M5SkhDvik6I_97VsdQs9-gSvPQ1U6FBr4a-a866Gu7xcXcPRLWU2UKmThfqAwJXoSS4cfDgap-frRec_Yqg3jZLyl29a-xNcQSsZN74ydv0W7swkFNrr8UHIlkoNQZjiDNJvqB2SxuIuBu3uGU2AiGqdasw9SBN9kDFXAA', true, 'unknown'),
    ('VITA', 'vk1.a.h8ZMyCgenUYgB6Ci8MKpi6AFVS9lXy4ndWrVPJu0BT4uncFFM3vmi8qJeUGpW-7X0DBhBWfQHs9qrIzo5CS2LkbpOnNo563B4XtY5DT-JPLYguCRQkmrEdcx7YQQQgzIALlB8bbQeyub32BJtZQvEs12xdcYXBHD85SUxJ2l6cuYjVj0gL5pqMR17xmlbxav3tx83eikViL1JH80Twipdw', true, 'unknown'),
    ('ELIS', '', false, 'unknown'),
    ('ALEX', '', false, 'unknown'),
    ('MAMA', '', false, 'unknown')
ON CONFLICT (name) DO NOTHING;

-- Комментарии к таблице
COMMENT ON TABLE vk_tokens IS 'VK API токены для динамического управления';
COMMENT ON COLUMN vk_tokens.name IS 'Имя токена (VALSTAN, OLGA, VITA, etc.)';
COMMENT ON COLUMN vk_tokens.token IS 'VK API токен';
COMMENT ON COLUMN vk_tokens.is_active IS 'Активен ли токен для использования';
COMMENT ON COLUMN vk_tokens.last_used IS 'Время последнего использования';
COMMENT ON COLUMN vk_tokens.last_validated IS 'Время последней валидации';
COMMENT ON COLUMN vk_tokens.validation_status IS 'Статус валидации: valid, invalid, unknown';
COMMENT ON COLUMN vk_tokens.error_message IS 'Сообщение об ошибке при валидации';
COMMENT ON COLUMN vk_tokens.permissions IS 'Права доступа токена (JSON)';
COMMENT ON COLUMN vk_tokens.user_info IS 'Информация о пользователе (JSON)';
