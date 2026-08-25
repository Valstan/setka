-- 082: клиентский флоу отложки — кто создал пост, группировка заказа, модерация.
-- Статусная машина расширяется ЗНАЧЕНИЯМИ той же колонки status:
--   'pending'  — создан не-trusted клиентом, ждёт одобрения владельца,
--                в VK НИЧЕГО не отправлено;
--   'rejected' — отклонён владельцем (moderation_comment виден клиенту).
-- publish_reconciler не задевается: он выбирает только status='scheduled'.
-- Применять с ON_ERROR_STOP=1.

BEGIN;

-- Кто создал пост из кабинета. NULL = оператор (как раньше).
ALTER TABLE ad_scheduled_posts ADD COLUMN IF NOT EXISTS created_by_user_id INTEGER
    REFERENCES radar_users(id) ON DELETE SET NULL;

-- Ярлык заказа: N районов одного сабмита несут один uuid. Только группировка
-- в UI — деньги на нём не считаются (правило «третий журнал = боль»).
ALTER TABLE ad_scheduled_posts ADD COLUMN IF NOT EXISTS order_ref VARCHAR(36);

-- Решение владельца по pending-посту.
ALTER TABLE ad_scheduled_posts ADD COLUMN IF NOT EXISTS moderated_at TIMESTAMP;
ALTER TABLE ad_scheduled_posts ADD COLUMN IF NOT EXISTS moderation_comment TEXT;

CREATE INDEX IF NOT EXISTS ix_ad_scheduled_client_status
    ON ad_scheduled_posts(client_id, status);

-- Довесить давно обещанный FK (модель: «пока без FK»). Клиентская изоляция
-- стоит на client_id — осиротевшие ссылки предварительно чистим.
UPDATE ad_scheduled_posts SET client_id = NULL
    WHERE client_id IS NOT NULL AND client_id NOT IN (SELECT id FROM ad_clients);
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_ad_scheduled_client'
    ) THEN
        ALTER TABLE ad_scheduled_posts ADD CONSTRAINT fk_ad_scheduled_client
            FOREIGN KEY (client_id) REFERENCES ad_clients(id) ON DELETE SET NULL;
    END IF;
END $$;

COMMIT;
