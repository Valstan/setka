-- 014: vk_tokens — поля для авто-fallback и временной блокировки токена.
--
-- Контекст. До этой миграции у токена была только пара ``is_active`` (хард-флаг,
-- руками) и ``validation_status`` (текстовая метка после последней проверки).
-- Этого не хватало для двух кейсов:
--   1. Пользователь-владелец токена временно заблокирован VK (например, на 24ч
--      за нарушение правил). Хочется выключить токен ровно на этот срок без
--      потери конфигурации, чтобы он сам «оттаял».
--   2. VK API вернул код 5 (invalid_token) / 17 (validation_required) / 29
--      (rate_limit_per_token) — это де-факто «токен сломан на ближайшее время».
--      Хочется автоматически перевести его в cooldown и пробовать следующего
--      кандидата из политики (modules/vk_token_router.TokenPolicy).
--
-- Добавляемые поля:
--   * disabled_until TIMESTAMP NULL — UTC момент, до которого токен НЕ выбирать
--     ни для одной операции. NULL = не заблокирован. Auto-disable пишет 24/1ч;
--     ручной disable через /api/tokens/{name}/disable — любой срок.
--   * last_error_code INTEGER NULL — последний VK error_code, который привёл к
--     записи в disabled_until (для UI и для отладки).
--   * last_error_at TIMESTAMP NULL — когда именно это произошло.
--   * consecutive_errors INTEGER DEFAULT 0 — счётчик подряд идущих ошибок; на
--     первой успешной операции сбрасывается. Поднимается каждым transient
--     fail'ом (HTTP 6 / network), не только 5/17/29.
--
-- Индекс ``idx_vk_tokens_active_window`` — для быстрого SELECT всех «активных
-- сейчас» токенов: ``WHERE is_active = TRUE AND (disabled_until IS NULL OR
-- disabled_until < NOW())``. На крошечной таблице (десятки строк) индекс не
-- критичен, но он попадает в EXPLAIN при join'ах с regions и помогает
-- читабельности.
--
-- Идемпотентна: повторное применение — no-op (IF NOT EXISTS на всех ALTER).

ALTER TABLE vk_tokens
    ADD COLUMN IF NOT EXISTS disabled_until TIMESTAMP NULL,
    ADD COLUMN IF NOT EXISTS last_error_code INTEGER NULL,
    ADD COLUMN IF NOT EXISTS last_error_at TIMESTAMP NULL,
    ADD COLUMN IF NOT EXISTS consecutive_errors INTEGER NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_vk_tokens_active_window
    ON vk_tokens(is_active, disabled_until);
