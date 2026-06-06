-- 032: единый роутер входящих ЛС — свой статус обработки + маршрут (Этап 1).
--
-- Баг потери данных (директива brain 2026-06-06). Входящие ЛС сообщества сканировал
-- ТОЛЬКО ad-кабинет (`scan_inbound_dm_ads`) и сохранял в `ad_requests` лишь рекламу;
-- не-рекламное ЛС не попадало никуда. При этом раздел «Уведомления» показывал лишь
-- ЖИВОЙ VK unread-счётчик (`messages.getConversations(filter=unread)`) и ничего не
-- хранил — поэтому, как только VK-флаг unread гас, не-рекламное сообщение исчезало
-- из нашего вида насовсем.
--
-- Фикс R1/R2: persist КАЖДОГО входящего ЛС в наш стор ДО классификации + собственный
-- статус обработки, не зависящий от VK read/unread. Переиспользуем `ad_requests` (как
-- и блок A, миграция 026 — там уже есть peer_id, last_message_id, тред-вью, ответ).
--
-- Что добавляем:
--   * route — где сейчас «живёт» сообщение: 'ad_cabinet' (реклама) | 'notifications'
--     (не реклама). Инвариант директивы R1: ровно одно «текущее место».
--   * handling_status — НАШ статус обработки: 'new' → 'in_progress' → 'done'. Источник
--     истины вместо VK read/unread (R2). В уведомлениях сообщение видно, пока не 'done'
--     (ставится оператором вручную — показ текста не делает его обработанным).
--   * handled_at — когда оператор пометил обработанным (аудит/архив).
--
-- Бэкфилл: существующие строки (вся реклама — предложка + ЛС) → route='ad_cabinet',
-- handling_status='new' (дефолты NOT NULL). Реклама остаётся в кабинете, ничего не
-- переезжает.
--
-- Идемпотентна: ADD COLUMN IF NOT EXISTS, CREATE INDEX IF NOT EXISTS, GRANT no-op.

ALTER TABLE ad_requests
    ADD COLUMN IF NOT EXISTS route VARCHAR(16) NOT NULL DEFAULT 'ad_cabinet';

ALTER TABLE ad_requests
    ADD COLUMN IF NOT EXISTS handling_status VARCHAR(16) NOT NULL DEFAULT 'new';

ALTER TABLE ad_requests
    ADD COLUMN IF NOT EXISTS handled_at TIMESTAMP;

-- Запрос ленты уведомлений: входящие ЛС, отданные в уведомления и ещё не обработанные.
-- Частичный индекс под (route, handling_status) только для origin='inbound_dm'.
CREATE INDEX IF NOT EXISTS idx_ad_requests_route_handling
    ON ad_requests(route, handling_status)
    WHERE origin = 'inbound_dm';

-- GRANT повторно — no-op (таблица из 021); оставляем для rebuild с нуля.
GRANT ALL PRIVILEGES ON TABLE ad_requests TO setka_user;
