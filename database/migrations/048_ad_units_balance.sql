-- Миграция 048: штучный учёт пакета публикаций + дедуп напоминания о перерасходе.
--
-- И2 непрерывной нити клиента (Раунд 4+). Решение владельца 2026-06-25: вести
-- баланс В ШТУКАХ (публикациях) — «купил N размещений, вышло M, осталось K» —
-- и напоминать в Telegram при ПЕРЕРАСХОДЕ (вышло больше оплаченного).
--
--   * ad_payments.units_paid  — за сколько публикаций эта оплата (пакет);
--                               NULL = штучно не указано (оплата только в рублях).
--   * ad_clients.spend_alerted_at — дедуп Telegram-напоминания о перерасходе
--                                   (сбрасывается в NULL при новой оплате — «доплатил
--                                   → можно напомнить снова»), по аналогии с paid_confirmed_at.
--
-- Аддитивно (ADD COLUMN), не лочит таблицу надолго, старый код колонок не читает —
-- безопасно применять на живом проде. Откат тривиален (DROP COLUMN), не обязателен.

ALTER TABLE ad_payments ADD COLUMN IF NOT EXISTS units_paid SMALLINT;
ALTER TABLE ad_clients  ADD COLUMN IF NOT EXISTS spend_alerted_at TIMESTAMP;

COMMENT ON COLUMN ad_payments.units_paid IS 'за сколько публикаций эта оплата (пакет); NULL = штучно не указано';
COMMENT ON COLUMN ad_clients.spend_alerted_at IS 'дедуп Telegram-напоминания о перерасходе пакета публикаций';
