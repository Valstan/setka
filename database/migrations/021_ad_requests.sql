-- 021: рекламный кабинет — таблица ad_requests (детект рекламы в предложке).
--
-- Контекст. В предложку наших главных ИНФО-групп регионов сыпется реклама.
-- Владелец вручную ходит в VK, копирует оффер, подставляет имя автора и
-- название сообщества, прикрепляет картинки — долго при большом потоке.
-- MVP «рекламного кабинета»: scanner ловит рекламу в предложке (алгоритм
-- AdvertisementFilter + предложка-сигналы), складывает заявку в эту таблицу,
-- UI /ad-cabinet готовит персонализированный ответ для отправки в 1 клик.
--
-- Таблица — backbone инбокса с жизненным циклом (status new→contacted→…),
-- который ДОЛЖЕН пережить рескан, когда предложенный пост уже опубликован/
-- удалён (Redis-снимки notifications для этого не годятся). Forward-compatible
-- с CRM фазы 3 (клиенты/оплаты по author_vk_id).
--
-- Идемпотентна: CREATE TABLE/INDEX IF NOT EXISTS, GRANT (no-op повторно),
-- DROP TRIGGER IF EXISTS перед CREATE TRIGGER (PG < 15 не знает
-- CREATE TRIGGER IF NOT EXISTS).

CREATE TABLE IF NOT EXISTS ad_requests (
    id                     BIGSERIAL PRIMARY KEY,
    region_id              INTEGER REFERENCES regions(id) ON DELETE SET NULL,
    community_vk_id        BIGINT  NOT NULL,            -- owner_id группы (отрицательный)
    community_name         VARCHAR(300),               -- снимок для подстановки/устойчивости
    vk_post_id             BIGINT  NOT NULL,           -- id предложенного поста (стабилен, пока pending)
    author_vk_id           BIGINT,                     -- from_id (signed; neg=группа)
    signer_id              BIGINT,                     -- человек-автор (если подписан)
    peer_id                BIGINT,                     -- цель для ЛС (обычно user)
    author_name            VARCHAR(300),               -- "Имя Фамилия" / имя группы; NULL если не резолвится
    author_is_group        BOOLEAN NOT NULL DEFAULT FALSE,
    text_snapshot          TEXT,
    attachments_json       JSONB,
    photo_urls_json        JSONB,                      -- прямые CDN-ссылки картинок поста (показ)
    score                  INTEGER NOT NULL DEFAULT 0,
    reasons_json           JSONB,                      -- list[str] причины классификации
    status                 VARCHAR(20) NOT NULL DEFAULT 'new',  -- new|contacted|skipped|published
    can_message            BOOLEAN,                    -- кэш isMessagesFromGroupAllowed
    can_message_checked_at TIMESTAMP,
    template_id            INTEGER,
    prepared_message       TEXT,
    message_attachments    TEXT,                       -- "photo123_456,photo123_457" после загрузки (кэш)
    via                    VARCHAR(30),                -- community-token|user-token|personal
    vk_message_id          BIGINT,
    detected_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    contacted_at           TIMESTAMP,
    created_at             TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at             TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Дедуп: одна заявка на (сообщество, предложенный пост).
CREATE UNIQUE INDEX IF NOT EXISTS uq_ad_requests_community_post
    ON ad_requests(community_vk_id, vk_post_id);
CREATE INDEX IF NOT EXISTS idx_ad_requests_status ON ad_requests(status);
CREATE INDEX IF NOT EXISTS idx_ad_requests_region ON ad_requests(region_id);
CREATE INDEX IF NOT EXISTS idx_ad_requests_detected_at ON ad_requests(detected_at DESC);

-- GRANT'ы пост-фактум: application-user setka_user не наследует права на новые
-- таблицы автоматически (см. миграции 008/009). GRANT идемпотентен.
GRANT ALL PRIVILEGES ON TABLE ad_requests TO setka_user;
GRANT USAGE, SELECT ON SEQUENCE ad_requests_id_seq TO setka_user;

-- Триггер updated_at (общая функция из миграций 003/011; CREATE OR REPLACE
-- идемпотентен).
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE 'plpgsql';

DROP TRIGGER IF EXISTS update_ad_requests_updated_at ON ad_requests;
CREATE TRIGGER update_ad_requests_updated_at
    BEFORE UPDATE ON ad_requests
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- Сид шаблона ответа-оффера (категория ad_offer), если ещё ни одного нет.
-- Плейсхолдеры {author_name} / {community_name} подставит message_builder.
-- Владелец правит текст и расценки на странице «Шаблоны ответов».
INSERT INTO message_templates (title, body, category, is_active, created_at, updated_at)
SELECT
    'Оффер: размещение рекламы',
    E'Здравствуйте, {author_name}!\n\nСпасибо за то, что предложили пост в сообщество «{community_name}». '
    || E'Размещение рекламы у нас — на коммерческой основе. Пришлю прайс и условия, подберём удобный формат.\n\n'
    || E'Напишите, что хотите разместить, — и мы всё оформим. С уважением, команда «{community_name}».',
    'ad_offer',
    TRUE,
    NOW(),
    NOW()
WHERE NOT EXISTS (SELECT 1 FROM message_templates WHERE category = 'ad_offer');
