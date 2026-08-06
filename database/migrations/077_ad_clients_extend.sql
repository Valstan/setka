-- 077: расширение ad_clients — телефон, телеграм, email, почтовый адрес
-- + author_vk_id optional (без VK тоже можно) с частичным уникальным индексом.
-- Применять с ON_ERROR_STOP=1.

BEGIN;

-- Новые контактные поля
ALTER TABLE ad_clients ADD COLUMN IF NOT EXISTS phone VARCHAR(50);
ALTER TABLE ad_clients ADD COLUMN IF NOT EXISTS telegram VARCHAR(100);
ALTER TABLE ad_clients ADD COLUMN IF NOT EXISTS email VARCHAR(200);
ALTER TABLE ad_clients ADD COLUMN IF NOT EXISTS postal_address TEXT;

-- author_vk_id → nullable (клиент может быть без VK)
ALTER TABLE ad_clients ALTER COLUMN author_vk_id DROP NOT NULL;

-- Пересоздаём уникальный индекс: теперь только для не-NULL значений
DROP INDEX IF EXISTS uq_ad_clients_author;
CREATE UNIQUE INDEX uq_ad_clients_author ON ad_clients(author_vk_id)
    WHERE author_vk_id IS NOT NULL;

COMMIT;
