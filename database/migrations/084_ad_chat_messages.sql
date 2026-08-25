-- 084: чат клиент↔владелец внутри кабинета рекламодателя (Фаза 2 кабинета).
-- Отдельная таблица, НЕ ad_interactions: у аудит-журнала другой жизненный цикл
-- (рендер в таймлайн, операторское удаление), а чату нужны unread-семантика и
-- выборка «после id N» под polling. VK-тред (client_thread) остаётся вторым
-- каналом для VK-клиентов.
-- Применять с ON_ERROR_STOP=1.

BEGIN;

CREATE TABLE IF NOT EXISTS ad_chat_messages (
    id BIGSERIAL PRIMARY KEY,
    client_id BIGINT NOT NULL REFERENCES ad_clients(id) ON DELETE CASCADE,
    sender VARCHAR(10) NOT NULL,          -- 'client' | 'owner'
    body TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT now(),
    read_at TIMESTAMP                     -- NULL = не прочитано противоположной стороной
);

CREATE INDEX IF NOT EXISTS ix_ad_chat_client_id ON ad_chat_messages(client_id, id);
CREATE INDEX IF NOT EXISTS ix_ad_chat_unread ON ad_chat_messages(client_id)
    WHERE read_at IS NULL;

COMMIT;
