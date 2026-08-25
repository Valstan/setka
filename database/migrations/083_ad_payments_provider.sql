-- 083: задел под платёжного провайдера (решение владельца 2026-08-25: MVP —
-- оплата вручную, но схема закладывается так, чтобы ЮKassa/СБП вставали без
-- перестройки). NULL provider = ручная/legacy оплата.
-- Применять с ON_ERROR_STOP=1.

BEGIN;

ALTER TABLE ad_payments ADD COLUMN IF NOT EXISTS provider VARCHAR(30);
ALTER TABLE ad_payments ADD COLUMN IF NOT EXISTS external_id VARCHAR(100);

-- Идемпотентность будущих вебхуков: один платёж провайдера — одна строка.
CREATE UNIQUE INDEX IF NOT EXISTS uq_ad_payments_provider_ext
    ON ad_payments(provider, external_id) WHERE external_id IS NOT NULL;

COMMIT;
