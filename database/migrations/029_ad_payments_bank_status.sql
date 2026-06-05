-- 029: оплаты рекламного кабинета — банк + статус ожидание/оплачено.
--
-- Контекст. Блок C (027) ввёл ad_payments как факт оплаты. Запрос владельца:
--   * фиксировать «ожидание оплаты» (поставили рекламу в отложку/опубликовали —
--     деньги ещё не пришли) с согласованной суммой; видеть должников;
--   * знать, на какой банк чаще платят (для планирования).
--
-- Поэтому:
--   * status  — 'awaiting' (ждём деньги) | 'paid' (получено). DEFAULT 'paid' —
--               старые записи и ручной ввод по умолчанию считаются оплаченными.
--   * bank    — банк зачисления (фикс-список в коде: AD_PAYMENT_BANKS).
--   * paid_confirmed_at — когда awaiting → paid (для отчётов/таймлайна).
--
-- amount остаётся согласованной суммой (для awaiting — ожидаемой).
-- Идемпотентна: ADD COLUMN IF NOT EXISTS.

ALTER TABLE ad_payments
    ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'paid';

ALTER TABLE ad_payments
    ADD COLUMN IF NOT EXISTS bank VARCHAR(40);

ALTER TABLE ad_payments
    ADD COLUMN IF NOT EXISTS paid_confirmed_at TIMESTAMP;

-- Частичный индекс по ожидающим оплатам — быстрый список должников.
CREATE INDEX IF NOT EXISTS idx_ad_payments_awaiting
    ON ad_payments(client_id) WHERE status = 'awaiting';
