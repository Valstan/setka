-- 008: message templates for VK community DM replies (etap 4b).
--
-- Operator-managed shortcuts for replying to direct messages from the UI.
-- Shared across all regions — same moderator answers for every community.
--
-- Идемпотентно: можно прогонять повторно без ошибок.

CREATE TABLE IF NOT EXISTS message_templates (
    id          SERIAL PRIMARY KEY,
    title       VARCHAR(120) NOT NULL,
    body        TEXT         NOT NULL,
    category    VARCHAR(50),
    is_active   BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_message_templates_category
    ON message_templates(category)
    WHERE category IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_message_templates_is_active
    ON message_templates(is_active)
    WHERE is_active = TRUE;
