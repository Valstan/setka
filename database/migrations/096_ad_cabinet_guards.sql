-- 096: аудит кабинета — гонки и дубли держит БД, клиент архивируется, а не
-- удаляется (Этап 1 программы «Кабинет под ключ», план 2026-09-05).
--
-- ЗАЧЕМ. Аудит 05.09 нашёл три места, где «ничего лишнего не публикуется и
-- оплаты не теряются» держалось только кодом:
--   1) реконсилер отложки выбирал строки без блокировки и коммитил один раз
--      после цикла — параллельный прогон (ручной вызов задачи поверх beat)
--      создавал вторые AdPublication и второй awaiting-платёж за тот же пост;
--   2) анти-спам «один рекламный пост клиента в одно сообщество в календарный
--      день МСК» был обычным SELECT COUNT до вставки — двойной сабмит
--      (кабинет + ВК-бот) проходил мимо него;
--   3) DELETE /clients/{id} каскадом стирал оплаты, пакеты и чат клиента, а его
--      pending-посты выпадали из очереди модерации (INNER JOIN), scheduled
--      выходили без счёта (client_id → NULL). Теперь клиент архивируется.
--
-- Замер прода 05.09 перед индексами: дублей публикаций/awaiting по одной
-- отложке — 0, двух активных постов клиента в одно сообщество в день — 0.
--
-- Идемпотентна (IF NOT EXISTS). Без now()-дефолтов. Без BEGIN/COMMIT — их даёт раннер.

ALTER TABLE ad_clients
    ADD COLUMN IF NOT EXISTS is_archived BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS ix_ad_clients_archived
    ON ad_clients(is_archived);

CREATE UNIQUE INDEX IF NOT EXISTS uq_ad_publications_per_post
    ON ad_publications(scheduled_post_id)
    WHERE scheduled_post_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_ad_payments_awaiting_per_post
    ON ad_payments(scheduled_post_id)
    WHERE scheduled_post_id IS NOT NULL AND status = 'awaiting';

CREATE UNIQUE INDEX IF NOT EXISTS uq_ad_sched_client_day_slot
    ON ad_scheduled_posts(client_id, community_vk_id, date(publish_date))
    WHERE client_id IS NOT NULL AND status IN ('pending', 'scheduled', 'published');
