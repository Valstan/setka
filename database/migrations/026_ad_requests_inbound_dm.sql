-- 026: рекламный кабинет, блок A — реклама во входящих ЛС сообщества.
--
-- Контекст. До сих пор кабинет ловил рекламу только в ПРЕДЛОЖКЕ (origin по
-- умолчанию). Но рекламодатели всё чаще пишут напрямую в сообщения сообщества
-- («размещу пост», «прайс», контакты). Блок A: beat-скан `messages.getConversations`
-- главных групп → классификация последнего входящего сообщения тем же
-- `classifier.classify` → заявка в `ad_requests` с `origin='inbound_dm'`.
--
-- Переиспользуем существующую таблицу `ad_requests` (021), а не заводим новую:
-- жизненный цикл (new→contacted→…), ответ оператора (`/send` уже умеет писать в
-- `peer_id`), офферные картинки — всё то же. Отличается лишь источник.
--
-- Что меняем:
--   1. `origin` — откуда заявка ('suggested' | 'inbound_dm'). Существующие строки
--      backfill'ятся дефолтом 'suggested' (NOT NULL DEFAULT).
--   2. `vk_post_id` → NULLABLE: у ЛС-заявки нет предложенного поста. Старый
--      уникальный индекс `uq_ad_requests_community_post(community_vk_id, vk_post_id)`
--      ОСТАВЛЯЕМ как есть — он по-прежнему дедупит предложку; строки ЛС с
--      vk_post_id=NULL в нём не конфликтуют (Postgres считает NULL различными).
--   3. Дедуп ЛС — отдельный ЧАСТИЧНЫЙ уникальный индекс по (community_vk_id,
--      peer_id) WHERE origin='inbound_dm': одна заявка на диалог с автором.
--   4. `last_message_id` — id последнего входящего сообщения (трекинг свежести
--      диалога, задел под тред-вью `messages.getHistory`).
--
-- Идемпотентна: ADD COLUMN/CREATE INDEX IF NOT EXISTS, DROP NOT NULL —
-- повторный прогон no-op.

ALTER TABLE ad_requests
    ADD COLUMN IF NOT EXISTS origin VARCHAR(20) NOT NULL DEFAULT 'suggested';

ALTER TABLE ad_requests
    ADD COLUMN IF NOT EXISTS last_message_id BIGINT;

-- У ЛС-заявки предложенного поста нет — снимаем NOT NULL.
ALTER TABLE ad_requests
    ALTER COLUMN vk_post_id DROP NOT NULL;

-- Дедуп входящих ЛС: одна заявка на (сообщество, автор-диалог).
CREATE UNIQUE INDEX IF NOT EXISTS uq_ad_requests_inbound_dm
    ON ad_requests(community_vk_id, peer_id)
    WHERE origin = 'inbound_dm';

-- Фильтрация инбокса по источнику (вкладки «предложка» / «личка»).
CREATE INDEX IF NOT EXISTS idx_ad_requests_origin ON ad_requests(origin);

-- GRANT'ы повторно — no-op (таблица уже была в 021); индексам отдельный GRANT
-- не нужен. Оставляем строку для единообразия и на случай rebuild с нуля.
GRANT ALL PRIVILEGES ON TABLE ad_requests TO setka_user;
