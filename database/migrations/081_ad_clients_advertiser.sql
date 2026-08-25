-- 081: клиентская половина рекламного кабинета — связка ЕСА-аккаунта с карточкой
-- клиента + модерация новых клиентов (решения владельца 2026-08-25).
-- Применять с ON_ERROR_STOP=1.

BEGIN;

-- Аккаунт ЕСА (radar_users), которому принадлежит карточка. NULL — карточка
-- заведена оператором, клиент в кабинет ещё не входил. Линковка только по
-- VK-identity либо при самозаводе (modules/ad_cabinet/advertiser_link.py) —
-- никогда из ручного ввода клиента.
ALTER TABLE ad_clients ADD COLUMN IF NOT EXISTS radar_user_id INTEGER
    REFERENCES radar_users(id) ON DELETE SET NULL;

-- Один аккаунт — максимум одна карточка (partial: NULL-ы не конфликтуют).
CREATE UNIQUE INDEX IF NOT EXISTS uq_ad_clients_radar_user
    ON ad_clients(radar_user_id) WHERE radar_user_id IS NOT NULL;

-- Модерация только новых клиентов: не-trusted публикует через одобрение
-- владельца; после AD_TRUST_AFTER_POSTS одобренных постов — trusted.
ALTER TABLE ad_clients ADD COLUMN IF NOT EXISTS trusted BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE ad_clients ADD COLUMN IF NOT EXISTS approved_posts_count SMALLINT NOT NULL DEFAULT 0;

COMMIT;
