-- 098: «Я оплатил» — клиент заявляет оплату (Этап 1, PR 1.7 аудита кабинета 2026-09-05).
--
-- ЗАЧЕМ. Клиент переводил деньги и писал об этом в чат (или никуда) — владелец
-- узнавал о переводе из выписки. Теперь кнопка «Я оплатил» в кабинете и в
-- ВК-боте ставит ad_payments.claimed_at на ожидающие счета: владелец видит
-- «заявил оплату» в списке кабинетов и подтверждает заказ одной кнопкой.
--
-- Идемпотентна (IF NOT EXISTS). UTC naive, как paid_confirmed_at. Без
-- BEGIN/COMMIT — их даёт раннер.

ALTER TABLE ad_payments ADD COLUMN IF NOT EXISTS claimed_at TIMESTAMP NULL;
COMMENT ON COLUMN ad_payments.claimed_at IS 'Клиент нажал «Я оплатил» (UTC naive); NULL — не заявлял';
