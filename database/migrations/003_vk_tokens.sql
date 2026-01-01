-- VK Tokens Management Table
-- Хранение токенов VK API в базе данных для динамического управления
--
-- IMPORTANT:
-- - Не вставлять реальные токены в миграции (git history).
-- - Заполнение/обновление токенов выполняется через admin endpoint/скрипт и хранится в БД.

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

-- Комментарии к таблице
COMMENT ON TABLE vk_tokens IS 'VK API токены для динамического управления';
COMMENT ON COLUMN vk_tokens.name IS 'Имя токена (VALSTAN, OLGA, VITA, etc.)';
COMMENT ON COLUMN vk_tokens.token IS 'VK API токен (секрет; не хранить в git)';
COMMENT ON COLUMN vk_tokens.is_active IS 'Активен ли токен для использования';
COMMENT ON COLUMN vk_tokens.last_used IS 'Время последнего использования';
COMMENT ON COLUMN vk_tokens.last_validated IS 'Время последней валидации';
COMMENT ON COLUMN vk_tokens.validation_status IS 'Статус валидации: valid, invalid, unknown';
COMMENT ON COLUMN vk_tokens.error_message IS 'Сообщение об ошибке при валидации';
COMMENT ON COLUMN vk_tokens.permissions IS 'Права доступа токена (JSON)';
COMMENT ON COLUMN vk_tokens.user_info IS 'Информация о пользователе (JSON)';

-- VK Tokens Management Table
-- Хранение токенов VK API в базе данных для динамического управления

CREATE TABLE IF NOT EXISTS vk_tokens (
    id SERIAL PRIMARY KEY,
    name VARCHAR(50) NOT NULL UNIQUE,  -- VALSTAN, OLGA, VITA, etc.
    token TEXT NOT NULL,               -- VK API токен (НЕ хранить в git, заполнять через админ-скрипт/ENV)
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
