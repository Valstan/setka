-- 027: рекламный кабинет, блок C — CRM (клиенты / оплаты / публикации).
--
-- Контекст. Блоки A (реклама в предложке + ЛС) и B (планировщик отложки) ловят
-- и публикуют рекламу, но НЕ ведут учёта: кто заказчик, сколько заплатил, что и
-- когда реально вышло. Блок C добавляет тонкий CRM-слой поверх существующих
-- `ad_requests` (021/026) и `ad_scheduled_posts` (025):
--
--   * ad_clients      — карточка рекламодателя (ключ `author_vk_id`); воронка
--                       сделки `stage` detected→contacted→scheduled→published→paid.
--   * ad_payments     — оплаты клиента (сумма, дата, способ; опц. ссылка на
--                       заявку/отложенный пост).
--   * ad_publications — что реально опубликовано (сообщество, пост, цена; опц.
--                       ссылка на заявку/отложенный пост).
--
-- Связи заявка/пост → клиент:
--   * ad_requests.client_id        — новая nullable-колонка + FK (SET NULL).
--   * ad_scheduled_posts.client_id — колонка уже была (025, задел без FK);
--                                    здесь навешиваем FK (обещано в 025).
--
-- Ключ агрегации — `author_vk_id`: один человек/группа = один клиент, его заявки
-- из предложки и ЛС сводятся в одну карточку. Резолв «заявка→клиент» делает
-- API (upsert по author_vk_id), БД лишь хранит связь.
--
-- Идемпотентна: CREATE TABLE/INDEX IF NOT EXISTS, ADD COLUMN IF NOT EXISTS,
-- FK через DO-блок с проверкой pg_constraint (ADD CONSTRAINT IF NOT EXISTS в
-- Postgres нет), GRANT повторно no-op, DROP TRIGGER IF EXISTS перед CREATE.

-- ----------------------------------------------------------------------
-- ad_clients — карточка рекламодателя
-- ----------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS ad_clients (
    id              BIGSERIAL PRIMARY KEY,
    author_vk_id    BIGINT NOT NULL,                 -- VK id заказчика (neg=группа); ключ сведения
    author_is_group BOOLEAN NOT NULL DEFAULT FALSE,
    name            VARCHAR(300),                    -- имя/название (снимок, редактируется оператором)
    vk_url          VARCHAR(300),                    -- удобная ссылка на профиль/группу
    contact         TEXT,                            -- телефон/почта/как связаться (заметки оператора)
    region_id       INTEGER REFERENCES regions(id) ON DELETE SET NULL,  -- основной регион размещения
    stage           VARCHAR(20) NOT NULL DEFAULT 'detected',  -- воронка сделки (см. ниже)
    notes           TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Один клиент на VK-id (сведение заявок предложки+ЛС в одну карточку).
CREATE UNIQUE INDEX IF NOT EXISTS uq_ad_clients_author ON ad_clients(author_vk_id);
CREATE INDEX IF NOT EXISTS idx_ad_clients_stage ON ad_clients(stage);
CREATE INDEX IF NOT EXISTS idx_ad_clients_region ON ad_clients(region_id);

-- Воронка `stage`: detected | contacted | scheduled | published | paid | lost.
-- detected   — заявка поймана, клиент заведён, контакта ещё не было;
-- contacted  — написали оффер;
-- scheduled  — пост(ы) запланированы (B1/B2);
-- published  — реклама вышла;
-- paid       — оплата получена;
-- lost       — отказ/слив.

-- ----------------------------------------------------------------------
-- ad_payments — оплаты
-- ----------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS ad_payments (
    id                 BIGSERIAL PRIMARY KEY,
    client_id          BIGINT NOT NULL REFERENCES ad_clients(id) ON DELETE CASCADE,
    amount             NUMERIC(12, 2) NOT NULL,
    method             VARCHAR(40),                  -- нал | карта | перевод | …
    ad_request_id      BIGINT,                       -- опц. за какую заявку
    scheduled_post_id  BIGINT,                       -- опц. за какой отложенный пост
    note               TEXT,
    paid_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_ad_payments_client ON ad_payments(client_id);
CREATE INDEX IF NOT EXISTS idx_ad_payments_paid_at ON ad_payments(paid_at DESC);

-- ----------------------------------------------------------------------
-- ad_publications — реально вышедшие публикации
-- ----------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS ad_publications (
    id                 BIGSERIAL PRIMARY KEY,
    client_id          BIGINT REFERENCES ad_clients(id) ON DELETE SET NULL,
    community_vk_id    BIGINT NOT NULL,              -- owner_id группы (отрицательный)
    vk_post_id         BIGINT,                       -- id опубликованного поста (если известен)
    region_id          INTEGER REFERENCES regions(id) ON DELETE SET NULL,
    ad_request_id      BIGINT,                       -- опц. из какой заявки
    scheduled_post_id  BIGINT,                       -- опц. из какого отложенного поста
    price              NUMERIC(12, 2),               -- согласованная цена размещения
    status             VARCHAR(20) NOT NULL DEFAULT 'published',  -- published | removed
    note               TEXT,
    published_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_ad_publications_client ON ad_publications(client_id);
CREATE INDEX IF NOT EXISTS idx_ad_publications_community ON ad_publications(community_vk_id);
CREATE INDEX IF NOT EXISTS idx_ad_publications_published_at ON ad_publications(published_at DESC);

-- ----------------------------------------------------------------------
-- Связи заявка/пост → клиент
-- ----------------------------------------------------------------------

ALTER TABLE ad_requests
    ADD COLUMN IF NOT EXISTS client_id BIGINT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_ad_requests_client'
    ) THEN
        ALTER TABLE ad_requests
            ADD CONSTRAINT fk_ad_requests_client
            FOREIGN KEY (client_id) REFERENCES ad_clients(id) ON DELETE SET NULL;
    END IF;
END$$;

CREATE INDEX IF NOT EXISTS idx_ad_requests_client ON ad_requests(client_id);

-- ad_scheduled_posts.client_id уже существует (025, задел) — навешиваем FK.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_ad_scheduled_posts_client'
    ) THEN
        ALTER TABLE ad_scheduled_posts
            ADD CONSTRAINT fk_ad_scheduled_posts_client
            FOREIGN KEY (client_id) REFERENCES ad_clients(id) ON DELETE SET NULL;
    END IF;
END$$;

CREATE INDEX IF NOT EXISTS idx_ad_scheduled_posts_client ON ad_scheduled_posts(client_id);

-- ----------------------------------------------------------------------
-- GRANT'ы пост-фактум: setka_user не наследует права на новые таблицы (008/009).
-- ----------------------------------------------------------------------

GRANT ALL PRIVILEGES ON TABLE ad_clients TO setka_user;
GRANT ALL PRIVILEGES ON TABLE ad_payments TO setka_user;
GRANT ALL PRIVILEGES ON TABLE ad_publications TO setka_user;
GRANT USAGE, SELECT ON SEQUENCE ad_clients_id_seq TO setka_user;
GRANT USAGE, SELECT ON SEQUENCE ad_payments_id_seq TO setka_user;
GRANT USAGE, SELECT ON SEQUENCE ad_publications_id_seq TO setka_user;

-- ----------------------------------------------------------------------
-- Триггеры updated_at (общая функция из 003/011; CREATE OR REPLACE идемпотентен).
-- ----------------------------------------------------------------------

CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE 'plpgsql';

DROP TRIGGER IF EXISTS update_ad_clients_updated_at ON ad_clients;
CREATE TRIGGER update_ad_clients_updated_at
    BEFORE UPDATE ON ad_clients
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_ad_publications_updated_at ON ad_publications;
CREATE TRIGGER update_ad_publications_updated_at
    BEFORE UPDATE ON ad_publications
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
