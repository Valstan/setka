-- 038: контент-радар Ф0.2 — источники, подписки, элементы ленты (план —
-- mailbox/to-brain/2026-06-12-content-radar-f0-plan.md, директива brain
-- 2026-06-11 «content-radar kickoff»).
--
-- Fan-out (требование директивы): источник поллится ОДИН раз на всех
-- подписчиков. radar_items — общий seen-стор: uniq (source_id, external_id)
-- даёт дедуп на уровне БД, поллер вставляет ON CONFLICT DO NOTHING.
--
-- type: 'vk' | 'rss' (Ф0.2); 'tg' добавится в Ф0.3 (через egress-relay) —
-- схема его уже вмещает, отдельной миграции не потребуется.
-- key — нормализованный идентификатор источника внутри типа:
--   vk  → owner_id стены строкой (отрицательный для сообществ), напр. '-218688001'
--   rss → канонизированный URL фида
--   tg  → username канала (Ф0.3)
--
-- Идемпотентна: CREATE TABLE/INDEX IF NOT EXISTS, GRANT повторно no-op.

CREATE TABLE IF NOT EXISTS radar_sources (
    id              BIGSERIAL PRIMARY KEY,
    type            VARCHAR(8)   NOT NULL,             -- vk|tg|rss
    key             VARCHAR(512) NOT NULL,             -- нормализованный ключ внутри типа
    title           VARCHAR(256),                      -- человекочитаемое имя (заполняется при добавлении)
    url             VARCHAR(1024),                     -- ссылка на источник для UI

    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
    last_polled_at  TIMESTAMP,                         -- последний успешный поллинг
    last_item_at    TIMESTAMP,                         -- published_at свежайшего элемента
    fail_count      INTEGER      NOT NULL DEFAULT 0,   -- подряд неудачных поллингов
    last_error      VARCHAR(512),                      -- текст последней ошибки (диагностика)

    created_at      TIMESTAMP    NOT NULL DEFAULT NOW(),

    UNIQUE (type, key)
);

CREATE TABLE IF NOT EXISTS radar_subscriptions (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT NOT NULL REFERENCES radar_users(id)   ON DELETE CASCADE,
    source_id       BIGINT NOT NULL REFERENCES radar_sources(id) ON DELETE CASCADE,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW(),

    UNIQUE (user_id, source_id)
);

CREATE INDEX IF NOT EXISTS ix_radar_subscriptions_source_id
    ON radar_subscriptions (source_id);

CREATE TABLE IF NOT EXISTS radar_items (
    id              BIGSERIAL PRIMARY KEY,
    source_id       BIGINT NOT NULL REFERENCES radar_sources(id) ON DELETE CASCADE,
    external_id     VARCHAR(256) NOT NULL,             -- id элемента внутри источника
    url             VARCHAR(1024),                     -- ссылка на оригинал
    title           VARCHAR(512),                      -- заголовок (RSS) / NULL (VK)
    text            TEXT,                              -- текст поста / summary фида
    media           JSON,                              -- [{type, url, ...}] — превью; байты архива — Ф0.4
    published_at    TIMESTAMP,                         -- время публикации в источнике
    fetched_at      TIMESTAMP NOT NULL DEFAULT NOW(),

    UNIQUE (source_id, external_id)
);

-- Лента читается «свежее сверху по подписанным источникам».
CREATE INDEX IF NOT EXISTS ix_radar_items_source_published
    ON radar_items (source_id, published_at DESC);

GRANT ALL PRIVILEGES ON TABLE radar_sources, radar_subscriptions, radar_items TO setka_user;
GRANT USAGE, SELECT ON SEQUENCE radar_sources_id_seq, radar_subscriptions_id_seq,
    radar_items_id_seq TO setka_user;
