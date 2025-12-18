-- Update VK Tokens table structure
-- Обновление структуры таблицы vk_tokens для управления токенами

-- Добавить новые колонки
ALTER TABLE vk_tokens 
ADD COLUMN IF NOT EXISTS last_used TIMESTAMP,
ADD COLUMN IF NOT EXISTS last_validated TIMESTAMP,
ADD COLUMN IF NOT EXISTS validation_status VARCHAR(20) DEFAULT 'unknown',
ADD COLUMN IF NOT EXISTS error_message TEXT,
ADD COLUMN IF NOT EXISTS permissions JSONB,
ADD COLUMN IF NOT EXISTS user_info JSONB;

-- Создать индексы для новых колонок
CREATE INDEX IF NOT EXISTS idx_vk_tokens_status ON vk_tokens(validation_status);

-- Обновить существующие записи
UPDATE vk_tokens SET 
    validation_status = 'unknown',
    is_active = true
WHERE validation_status IS NULL;

-- Вставить токены из конфигурации (если их еще нет)
INSERT INTO vk_tokens (name, token, is_active, validation_status) 
VALUES 
    ('VALSTAN', 'vk1.a.nv5IKyDlt15vjgcELAdi5c9mduzY9Wob160azxF_AOblv45fu-sgeDxgwsdM0BKWlemtdHaIj27ap6e2Nt-bQ5JVQAkdUplOV9uRi9Kqa3nZRCH-lkmpKrLt6o_garU9CPbZu9KZVD-iU2mQuknY68bZasL74X8TZ_R2zcLl_2Y3XmU1TFR3wsP4M6Xju9IN2Ygo3V_05Spe1_4mVN2roA', true, 'unknown'),
    ('OLGA', 'vk1.a.YB3vu9mP072pkadsec7VVBDaIjke_VByDUks3QnLaWsbbu28M5SkhDvik6I_97VsdQs9-gSvPQ1U6FBr4a-a866Gu7xcXcPRLWU2UKmThfqAwJXoSS4cfDgap-frRec_Yqg3jZLyl29a-xNcQSsZN74ydv0W7swkFNrr8UHIlkoNQZjiDNJvqB2SxuIuBu3uGU2AiGqdasw9SBN9kDFXAA', true, 'unknown'),
    ('VITA', 'vk1.a.h8ZMyCgenUYgB6Ci8MKpi6AFVS9lXy4ndWrVPJu0BT4uncFFM3vmi8qJeUGpW-7X0DBhBWfQHs9qrIzo5CS2LkbpOnNo563B4XtY5DT-JPLYguCRQkmrEdcx7YQQQgzIALlB8bbQeyub32BJtZQvEs12xdcYXBHD85SUxJ2l6cuYjVj0gL5pqMR17xmlbxav3tx83eikViL1JH80Twipdw', true, 'unknown'),
    ('ELIS', '', false, 'unknown'),
    ('ALEX', '', false, 'unknown'),
    ('MAMA', '', false, 'unknown')
ON CONFLICT (name) DO UPDATE SET
    token = EXCLUDED.token,
    is_active = EXCLUDED.is_active,
    validation_status = EXCLUDED.validation_status;

-- Комментарии к новым колонкам
COMMENT ON COLUMN vk_tokens.last_used IS 'Время последнего использования';
COMMENT ON COLUMN vk_tokens.last_validated IS 'Время последней валидации';
COMMENT ON COLUMN vk_tokens.validation_status IS 'Статус валидации: valid, invalid, unknown';
COMMENT ON COLUMN vk_tokens.error_message IS 'Сообщение об ошибке при валидации';
COMMENT ON COLUMN vk_tokens.permissions IS 'Права доступа токена (JSON)';
COMMENT ON COLUMN vk_tokens.user_info IS 'Информация о пользователе (JSON)';
