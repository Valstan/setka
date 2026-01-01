-- Update VK Tokens table structure
-- Обновление структуры таблицы vk_tokens для управления токенами
--
-- IMPORTANT:
-- - Не хранить реальные токены в миграциях.

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

-- Комментарии к новым колонкам
COMMENT ON COLUMN vk_tokens.last_used IS 'Время последнего использования';
COMMENT ON COLUMN vk_tokens.last_validated IS 'Время последней валидации';
COMMENT ON COLUMN vk_tokens.validation_status IS 'Статус валидации: valid, invalid, unknown';
COMMENT ON COLUMN vk_tokens.error_message IS 'Сообщение об ошибке при валидации';
COMMENT ON COLUMN vk_tokens.permissions IS 'Права доступа токена (JSON)';
COMMENT ON COLUMN vk_tokens.user_info IS 'Информация о пользователе (JSON)';

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

-- Комментарии к новым колонкам
COMMENT ON COLUMN vk_tokens.last_used IS 'Время последнего использования';
COMMENT ON COLUMN vk_tokens.last_validated IS 'Время последней валидации';
COMMENT ON COLUMN vk_tokens.validation_status IS 'Статус валидации: valid, invalid, unknown';
COMMENT ON COLUMN vk_tokens.error_message IS 'Сообщение об ошибке при валидации';
COMMENT ON COLUMN vk_tokens.permissions IS 'Права доступа токена (JSON)';
COMMENT ON COLUMN vk_tokens.user_info IS 'Информация о пользователе (JSON)';
