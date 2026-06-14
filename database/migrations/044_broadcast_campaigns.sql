-- 044: сетевая рассылка постов — внутренний планировщик-публикатор
-- (директива brain 2026-06-14 network-broadcast-internal-scheduler).
--
-- Канон владельца: публикуем ИЗ ПРОГРАММЫ своим беатом (wall.post немедленно),
-- НЕ кладём в VK-отложку — чтобы правка текста/расписания/очереди жила в одном
-- интерфейсе SARAFAN, а не разбредалась по сотням отложек VK.
--
-- Сущности:
--   broadcast_campaigns    — кампания (текст+медиа + расписание + повтор),
--                            редактируемая до и между публикациями;
--   broadcast_targets      — набор целевых сообществ (по умолчанию все паблики
--                            сети = активные регионы с vk_group_id);
--   broadcast_publications — per-(цель, прогон) защёлка: одна публикация на
--                            (campaign, group, run_index) — идемпотентность под
--                            конкурентным беатом (UNIQUE + ON CONFLICT claim).
--
-- Время: scheduled_at / next_run_at — МСК wall-clock naive (как publish_date в
-- ad-CRM); диспетчер сравнивает с МСК-now. published_at — UTC.
--
-- Идемпотентна: CREATE TABLE/INDEX IF NOT EXISTS, GRANT повторно no-op.

CREATE TABLE IF NOT EXISTS broadcast_campaigns (
    id                    BIGSERIAL PRIMARY KEY,
    title                 VARCHAR(300) NOT NULL DEFAULT '',
    body                  TEXT NOT NULL DEFAULT '',
    image_names           JSON,                              -- имена загруженных картинок
    attachments           TEXT,                              -- кэш "photo<o>_<id>,…" (залито 1 раз)
    status                VARCHAR(20) NOT NULL DEFAULT 'draft',  -- draft|scheduled|done|cancelled
    scheduled_at          TIMESTAMP,                         -- МСК wall-clock: первый запуск
    repeat_count          INTEGER NOT NULL DEFAULT 1,        -- сколько раз разослать (≥1)
    repeat_interval_hours DOUBLE PRECISION NOT NULL DEFAULT 24,  -- интервал между запусками
    runs_done             INTEGER NOT NULL DEFAULT 0,        -- завершённых прогонов
    next_run_at           TIMESTAMP,                         -- МСК wall-clock: следующий запуск
    vary_per_target       BOOLEAN NOT NULL DEFAULT FALSE,    -- hook лёгкой вариации (off по умолч.)
    created_at            TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),
    updated_at            TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc')
);
CREATE INDEX IF NOT EXISTS ix_broadcast_campaigns_status ON broadcast_campaigns (status);
CREATE INDEX IF NOT EXISTS ix_broadcast_campaigns_next_run ON broadcast_campaigns (next_run_at);

CREATE TABLE IF NOT EXISTS broadcast_targets (
    id          BIGSERIAL PRIMARY KEY,
    campaign_id BIGINT NOT NULL REFERENCES broadcast_campaigns (id) ON DELETE CASCADE,
    group_id    BIGINT NOT NULL,         -- owner_id группы VK (как regions.vk_group_id)
    name        VARCHAR(300),            -- снимок имени паблика/региона (для UI)
    CONSTRAINT uq_broadcast_target UNIQUE (campaign_id, group_id)
);
CREATE INDEX IF NOT EXISTS ix_broadcast_targets_campaign ON broadcast_targets (campaign_id);

CREATE TABLE IF NOT EXISTS broadcast_publications (
    id           BIGSERIAL PRIMARY KEY,
    campaign_id  BIGINT NOT NULL REFERENCES broadcast_campaigns (id) ON DELETE CASCADE,
    group_id     BIGINT NOT NULL,
    run_index    INTEGER NOT NULL DEFAULT 0,   -- какой по счёту прогон (0-based)
    status       VARCHAR(20) NOT NULL DEFAULT 'pending',  -- pending|published|error
    vk_post_id   BIGINT,
    post_url     VARCHAR(300),
    error        TEXT,
    published_at TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),
    CONSTRAINT uq_broadcast_publication UNIQUE (campaign_id, group_id, run_index)
);
CREATE INDEX IF NOT EXISTS ix_broadcast_publications_campaign ON broadcast_publications (campaign_id);

GRANT ALL PRIVILEGES ON TABLE broadcast_campaigns TO setka_user;
GRANT ALL PRIVILEGES ON TABLE broadcast_targets TO setka_user;
GRANT ALL PRIVILEGES ON TABLE broadcast_publications TO setka_user;
GRANT USAGE, SELECT ON SEQUENCE broadcast_campaigns_id_seq TO setka_user;
GRANT USAGE, SELECT ON SEQUENCE broadcast_targets_id_seq TO setka_user;
GRANT USAGE, SELECT ON SEQUENCE broadcast_publications_id_seq TO setka_user;
