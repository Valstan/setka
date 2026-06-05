-- 028: рекламный кабинет — журнал взаимодействий (audit-log / таймлайн).
--
-- Контекст. Кабинет (блоки A/B/C) ловит, отвечает, планирует и публикует
-- рекламу, но НЕ ведёт хронологию: оператор не видит, что он уже отвечал
-- клиенту, когда и во сколько что-то делал, за что и сколько заплачено. Эта
-- таблица — единый журнал событий поверх существующих сущностей. Каждое
-- действие (ответ, смена статуса, отложка, публикация, оплата, ручная заметка)
-- пишется сюда через `modules.ad_cabinet.interaction_log.log_interaction`.
--
-- Связи nullable — событие может относиться к клиенту, заявке, отложке,
-- публикации и/или оплате одновременно или ни к чему (ручная заметка).
-- `client_id` ON DELETE SET NULL — удаление клиента не стирает историю
-- (она остаётся как осиротевшие записи; UI показывает по client_id).
--
-- Идемпотентна: CREATE TABLE/INDEX IF NOT EXISTS, GRANT повторно no-op.

CREATE TABLE IF NOT EXISTS ad_interactions (
    id                BIGSERIAL PRIMARY KEY,
    client_id         BIGINT REFERENCES ad_clients(id) ON DELETE SET NULL,
    ad_request_id     BIGINT,                          -- опц. заявка предложки/ЛС
    scheduled_post_id BIGINT,                          -- опц. отложенный пост
    publication_id    BIGINT,                          -- опц. вышедшая публикация
    payment_id        BIGINT,                          -- опц. оплата
    kind              VARCHAR(40) NOT NULL,            -- reply_sent|status_changed|scheduled|cancelled|published|payment_added|payment_paid|payment_deleted|publication_deleted|contacted|note|...
    summary           TEXT,                            -- человекочитаемое описание события
    meta_json         JSON,                            -- произвольные детали (via, amount, ...)
    actor             VARCHAR(40) NOT NULL DEFAULT 'operator',  -- operator | system (beat-таски)
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Главный индекс таймлайна: события клиента, свежие сверху.
CREATE INDEX IF NOT EXISTS idx_ad_interactions_client
    ON ad_interactions(client_id, created_at DESC);
-- Для бэкфилла client_id при upsert заявки → клиент.
CREATE INDEX IF NOT EXISTS idx_ad_interactions_request
    ON ad_interactions(ad_request_id);

-- GRANT'ы: setka_user не наследует права на новые таблицы (008/009).
GRANT ALL PRIVILEGES ON TABLE ad_interactions TO setka_user;
GRANT USAGE, SELECT ON SEQUENCE ad_interactions_id_seq TO setka_user;
