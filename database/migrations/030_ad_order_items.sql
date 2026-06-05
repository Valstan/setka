-- 030: рекламный кабинет — позиции заказа клиента (что заказано / будет опубликовано).
--
-- Контекст. CRM (027) знает оплаты и факт публикаций, но не «заказ»: сколько и
-- каких реклам клиент заказал на период, одни и те же или разные. Запрос
-- владельца — вести список позиций заказа, подтягивая из предложки или вписывая
-- руками, всё редактируемо/удаляемо.
--
-- ad_order_items — тонкий список позиций поверх клиента:
--   * description       — что за реклама (текст; из предложки или вручную);
--   * quantity          — сколько размещений этой позиции;
--   * period_start/_end — на какой период заказано;
--   * status            — planned | scheduled | published | cancelled;
--   * ссылки (nullable) на заявку/отложку/публикацию — откуда позиция и чем
--                         реализована.
--
-- Идемпотентна: CREATE TABLE/INDEX IF NOT EXISTS, GRANT повторно no-op,
-- DROP TRIGGER IF EXISTS перед CREATE.

CREATE TABLE IF NOT EXISTS ad_order_items (
    id                BIGSERIAL PRIMARY KEY,
    client_id         BIGINT NOT NULL REFERENCES ad_clients(id) ON DELETE CASCADE,
    ad_request_id     BIGINT,                          -- опц. связь с предложкой
    scheduled_post_id BIGINT,                          -- опц. чем запланировано
    publication_id    BIGINT,                          -- опц. чем опубликовано
    description       TEXT,                            -- что за реклама
    quantity          INTEGER NOT NULL DEFAULT 1,      -- сколько размещений
    period_start      DATE,
    period_end        DATE,
    status            VARCHAR(20) NOT NULL DEFAULT 'planned',  -- planned|scheduled|published|cancelled
    note              TEXT,
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_ad_order_items_client ON ad_order_items(client_id);

GRANT ALL PRIVILEGES ON TABLE ad_order_items TO setka_user;
GRANT USAGE, SELECT ON SEQUENCE ad_order_items_id_seq TO setka_user;

-- Триггер updated_at (общая функция из 003/011).
DROP TRIGGER IF EXISTS update_ad_order_items_updated_at ON ad_order_items;
CREATE TRIGGER update_ad_order_items_updated_at
    BEFORE UPDATE ON ad_order_items
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
