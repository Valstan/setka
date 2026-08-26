-- 086: пакеты постов клиентов кабинета (заказ владельца 2026-08-26).
--
-- Виды: free_promo (акция «бесплатная реклама местным», бессрочно, малмыжским
-- site_ad = размещение на сайте вМалмыже.рф вручную), prepaid («оплатил N
-- постов», доступен после галочки paid_at, без срока), postpaid (месячный,
-- доступен сразу, по истечении без оплаты — блок; продление только вручную).
--
-- Посты в счёт пакета несут ad_scheduled_posts.package_id и price=0 —
-- реконсилер awaiting-платежей по ним не создаёт (деньги живут на пакете).
--
-- Идемпотентна (IF NOT EXISTS). Применять с ON_ERROR_STOP=1.

BEGIN;

CREATE TABLE IF NOT EXISTS ad_client_packages (
    id BIGSERIAL PRIMARY KEY,
    client_id BIGINT NOT NULL REFERENCES ad_clients(id) ON DELETE CASCADE,
    kind VARCHAR(20) NOT NULL,
    posts_total SMALLINT NOT NULL,
    posts_used SMALLINT NOT NULL DEFAULT 0,
    price NUMERIC(10, 2) NOT NULL DEFAULT 0,
    period_start DATE,
    period_end DATE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    paid_at TIMESTAMP,
    site_ad BOOLEAN NOT NULL DEFAULT FALSE,
    site_ad_done_at TIMESTAMP,
    note TEXT,
    created_at TIMESTAMP DEFAULT (now() AT TIME ZONE 'utc')
);

CREATE INDEX IF NOT EXISTS ix_ad_client_packages_client
    ON ad_client_packages(client_id, is_active);

ALTER TABLE ad_scheduled_posts ADD COLUMN IF NOT EXISTS package_id BIGINT
    REFERENCES ad_client_packages(id) ON DELETE SET NULL;

COMMIT;
