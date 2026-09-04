-- 097: деньги пакетов — в ad_payments (Этап 1, PR 1.4 аудита кабинета 2026-09-05).
--
-- ЗАЧЕМ. Оплата пакета жила только в ad_client_packages.price и не попадала
-- ни в баланс клиента (balance.py: paid = Σ ad_payments.status='paid'), ни в
-- «оплачено» списка кабинетов, ни в список клиентов CRM. Клиент, заплативший
-- за пакет, выглядел как «вышло 10 · оплачено 0 ₽». Теперь create_package /
-- mark-paid пишут AdPayment(provider='package', external_id=<id пакета>),
-- а этот бэкфилл добирает уже оплаченные пакеты.
--
-- Схему не меняет. Идемпотентна: NOT EXISTS + частичный уникум
-- uq_ad_payments_provider_ext (083). Бесплатный промо и нулевая цена не
-- порождают платежей. Без BEGIN/COMMIT — их даёт раннер.

INSERT INTO ad_payments (
    client_id, amount, status, units_paid, provider, external_id, note,
    paid_at, paid_confirmed_at, created_at
)
SELECT
    p.client_id,
    p.price,
    'paid',
    NULLIF(p.posts_total, 0),
    'package',
    p.id::text,
    'пакет #' || p.id || ' (' || p.kind || ')',
    p.paid_at,
    p.paid_at,
    (now() AT TIME ZONE 'utc')
FROM ad_client_packages p
WHERE p.paid_at IS NOT NULL
  AND p.price > 0
  AND p.kind <> 'free_promo'
  AND NOT EXISTS (
      SELECT 1 FROM ad_payments a
      WHERE a.provider = 'package' AND a.external_id = p.id::text
  );
