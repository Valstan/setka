-- 100: рассылка рекламного предложения (Этап 4 программы «Кабинет под ключ», 2026-09-05).
--
-- ЗАЧЕМ. Владелец хочет написать всем, кто за полгода писал про рекламу
-- (предложка и личка ИНФО-сообществ), и завести им кабинеты с промо-пакетом.
-- Автоматически — только там, где VK это разрешает (ответ в диалог с
-- сообществом; авторы предложки с разрешёнными сообщениями); остальные — в
-- ручной список с deeplink. Кампания: шаблон, лимиты (30/сутки на сообщество,
-- 150/сутки всего), тихие часы 21–9 МСК, dry-run по умолчанию, пауза по 9/14.
--
-- Идемпотентна (IF NOT EXISTS). Таймстампы UTC naive. Без BEGIN/COMMIT — их даёт раннер.

CREATE TABLE IF NOT EXISTS ad_outreach_campaigns (
    id BIGSERIAL PRIMARY KEY,
    title VARCHAR(200) NOT NULL,
    template_id INTEGER NULL REFERENCES message_templates(id) ON DELETE SET NULL,
    months_back SMALLINT NOT NULL DEFAULT 6,
    per_community_daily SMALLINT NOT NULL DEFAULT 30,
    total_daily SMALLINT NOT NULL DEFAULT 150,
    quiet_start SMALLINT NOT NULL DEFAULT 21,
    quiet_end SMALLINT NOT NULL DEFAULT 9,
    dry_run BOOLEAN NOT NULL DEFAULT TRUE,
    status VARCHAR(20) NOT NULL DEFAULT 'draft',
    paused_until TIMESTAMP NULL,
    paused_reason TEXT NULL,
    images_json JSON NULL,
    note TEXT NULL,
    started_at TIMESTAMP NULL,
    finished_at TIMESTAMP NULL,
    created_at TIMESTAMP NULL,
    updated_at TIMESTAMP NULL
);
COMMENT ON TABLE ad_outreach_campaigns IS 'Кампании рассылки рекламного оффера (Этап 4, 2026-09-05)';

CREATE TABLE IF NOT EXISTS ad_outreach_recipients (
    id BIGSERIAL PRIMARY KEY,
    campaign_id BIGINT NOT NULL REFERENCES ad_outreach_campaigns(id) ON DELETE CASCADE,
    vk_user_id BIGINT NOT NULL,
    community_vk_id BIGINT NOT NULL,
    ad_request_id BIGINT NULL,
    client_id BIGINT NULL REFERENCES ad_clients(id) ON DELETE SET NULL,
    name VARCHAR(200) NULL,
    origin VARCHAR(20) NULL,
    mode VARCHAR(10) NOT NULL DEFAULT 'manual',
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    body TEXT NULL,
    attempts SMALLINT NOT NULL DEFAULT 0,
    claimed_at TIMESTAMP NULL,
    sent_at TIMESTAMP NULL,
    vk_message_id BIGINT NULL,
    error_code INTEGER NULL,
    error TEXT NULL,
    created_at TIMESTAMP NULL,
    updated_at TIMESTAMP NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_ad_outreach_recipient ON ad_outreach_recipients (campaign_id, vk_user_id);
CREATE INDEX IF NOT EXISTS ix_ad_outreach_recipient_status ON ad_outreach_recipients (campaign_id, status);
CREATE INDEX IF NOT EXISTS ix_ad_outreach_recipient_user ON ad_outreach_recipients (vk_user_id);
CREATE INDEX IF NOT EXISTS ix_ad_outreach_recipient_sent ON ad_outreach_recipients (status, sent_at);

CREATE TABLE IF NOT EXISTS ad_outreach_blacklist (
    vk_user_id BIGINT PRIMARY KEY,
    reason TEXT NULL,
    until TIMESTAMP NULL,
    created_at TIMESTAMP NULL
);
COMMENT ON TABLE ad_outreach_blacklist IS 'Кому оффер не слать никогда/до даты (просьба, жалоба, 900)';
