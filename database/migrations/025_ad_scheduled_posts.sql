-- 025: рекламный кабинет — таблица ad_scheduled_posts (планировщик отложки).
--
-- Контекст. Из рекламного кабинета оператор формирует график постов по датам и
-- отправляет их в VK-«Отложенные записи» (postponed) целевого сообщества: VK сам
-- публикует в назначенное время. Три сценария (B1):
--   1) один текст+картинки → несколько дат (повторная раскладка);
--   2) разные посты → по одной дате каждый;
--   3) из заявки/предложки → подтянуть содержимое и расставить даты.
--
-- Эта таблица — реестр запланированного: что, куда, когда, с какими тумблерами
-- (from_group/signed/комментарии), и id поста в VK-отложке для отмены/трекинга.
-- Публикация идёт через VKPublisher.publish_digest(publish_date=…) (seam B1-a).
--
-- ВАЖНО про время: `publish_date` хранится как МСК wall-clock (ровно то, что ввёл
-- оператор). В unix для VK конвертирует API-слой (МСК = UTC+3, без DST).
--
-- Forward-compatible с учётом (фаза C): client_id/price пока nullable без FK —
-- FK на ad_clients добавит миграция фазы C.
--
-- Идемпотентна: CREATE TABLE/INDEX IF NOT EXISTS, GRANT (no-op повторно),
-- DROP TRIGGER IF EXISTS перед CREATE TRIGGER.

CREATE TABLE IF NOT EXISTS ad_scheduled_posts (
    id                   BIGSERIAL PRIMARY KEY,
    community_vk_id      BIGINT  NOT NULL,             -- owner_id целевой группы (отрицательный)
    region_id            INTEGER REFERENCES regions(id) ON DELETE SET NULL,
    text                 TEXT,
    image_names          JSONB,                        -- выбранные офферные картинки (имена файлов)
    attachments          TEXT,                         -- "photo<o>_<id>,…" после заливки на стену (кэш)
    publish_date         TIMESTAMP NOT NULL,           -- МСК wall-clock (что ввёл оператор)
    from_group           BOOLEAN NOT NULL DEFAULT TRUE,
    signed               BOOLEAN NOT NULL DEFAULT FALSE,
    comments_enabled     BOOLEAN NOT NULL DEFAULT TRUE,
    status               VARCHAR(20) NOT NULL DEFAULT 'draft',  -- draft|scheduled|published|failed|cancelled
    vk_postponed_post_id BIGINT,                       -- id поста в VK-отложке (отмена/трекинг)
    source_ad_request_id BIGINT,                       -- если пришёл из заявки/предложки
    client_id            BIGINT,                       -- задел под учёт (фаза C); FK добавим в C
    price                NUMERIC(12, 2),               -- задел под учёт (фаза C)
    error_message        TEXT,
    created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_ad_scheduled_community ON ad_scheduled_posts(community_vk_id);
CREATE INDEX IF NOT EXISTS idx_ad_scheduled_publish_date ON ad_scheduled_posts(publish_date);
CREATE INDEX IF NOT EXISTS idx_ad_scheduled_status ON ad_scheduled_posts(status);

-- GRANT'ы пост-фактум: setka_user не наследует права на новые таблицы (см. 008/009).
GRANT ALL PRIVILEGES ON TABLE ad_scheduled_posts TO setka_user;
GRANT USAGE, SELECT ON SEQUENCE ad_scheduled_posts_id_seq TO setka_user;

-- Триггер updated_at (общая функция из 003/011; CREATE OR REPLACE идемпотентен).
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE 'plpgsql';

DROP TRIGGER IF EXISTS update_ad_scheduled_posts_updated_at ON ad_scheduled_posts;
CREATE TRIGGER update_ad_scheduled_posts_updated_at
    BEFORE UPDATE ON ad_scheduled_posts
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
