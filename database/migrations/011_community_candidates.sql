-- 011: community_candidates + Region.vk_city_id/center_city + Community.health_*
--
-- Поддержка модуля авто-регистрации регионов и сообществ (см. DEV_HISTORY
-- 2026-05-22 — big idea). Wizard добавления нового района → Celery-таска
-- ищет VK-сообщества (geo + ключевики) → Groq AI-категоризатор предлагает
-- тематику → модератор через UI approve/reject из `community_candidates`.
--
-- Идемпотентна: повторное применение — no-op (все ALTER через IF NOT
-- EXISTS, индексы через IF NOT EXISTS / DO-блок).

-- ─────────────────────────────────────────────────────────────────
-- 1. regions: где регион расположен в VK (для groups.search).
-- ─────────────────────────────────────────────────────────────────

-- vk_city_id — численный city_id VK API (resolve через database.getCities).
-- Если NULL — geo-search пропускается, поиск только по ключевикам.
ALTER TABLE regions ADD COLUMN IF NOT EXISTS vk_city_id INTEGER;

-- center_city — human-readable название центра района ("Малмыж"), для
-- построения keyword-запросов ("Малмыж новости", "Малмыж объявления").
ALTER TABLE regions ADD COLUMN IF NOT EXISTS center_city VARCHAR(200);

-- ─────────────────────────────────────────────────────────────────
-- 2. communities: health-флаги и метки времени проверок.
-- ─────────────────────────────────────────────────────────────────

-- health_status: active / dormant / dead / changed_category.
-- - active           — постит регулярно, категория не сместилась.
-- - dormant          — последний пост старше N дней (настройка региона).
-- - dead             — VK error 15/100/203 при последнем check'е (группа
--                      удалена/заблокирована/закрыт доступ).
-- - changed_category — AI обнаружил что характер постов сместился; UI
--                      подсветит и предложит обновить категорию.
ALTER TABLE communities ADD COLUMN IF NOT EXISTS health_status VARCHAR(30) DEFAULT 'active';

-- last_post_at — timestamp последнего поста на стене сообщества (заполняет
-- recheck-таска через wall.get(count=1)).
ALTER TABLE communities ADD COLUMN IF NOT EXISTS last_post_at TIMESTAMP;

-- checked_at — когда последний раз шла health-проверка (отдельно от
-- existing last_checked — он про парс-cycle).
ALTER TABLE communities ADD COLUMN IF NOT EXISTS checked_at TIMESTAMP;

-- suggested_category — если AI считает что категория устарела, кладёт сюда
-- предлагаемую. Модератор может одним кликом применить.
ALTER TABLE communities ADD COLUMN IF NOT EXISTS suggested_category VARCHAR(50);

CREATE INDEX IF NOT EXISTS idx_communities_health
    ON communities(health_status);

-- Composite UNIQUE(region_id, vk_id) — в рамках одного региона vk_id уникален.
-- Между регионами одна и та же группа может быть привязана к нескольким
-- (например, областная группа в нескольких районах).
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE schemaname = 'public'
          AND indexname = 'uq_communities_region_vk'
    ) THEN
        CREATE UNIQUE INDEX uq_communities_region_vk
            ON communities(region_id, vk_id);
    END IF;
END$$;

-- ─────────────────────────────────────────────────────────────────
-- 3. community_candidates — буфер discovery до approve.
-- ─────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS community_candidates (
    id SERIAL PRIMARY KEY,
    region_id INTEGER NOT NULL REFERENCES regions(id) ON DELETE CASCADE,

    -- VK group info (snapshot на момент discovery)
    vk_id INTEGER NOT NULL,                 -- abs(group_id), положительный
    name VARCHAR(300) NOT NULL,
    screen_name VARCHAR(100),
    photo_url TEXT,
    description TEXT,
    members_count INTEGER,

    -- AI suggestions (заполняются ai_categorizer'ом)
    ai_category VARCHAR(50),                -- admin/novost/reklama/sosed/kultura/sport/detsad/other
    ai_confidence INTEGER,                  -- 0-100
    ai_reasoning TEXT,                      -- одна фраза, почему именно эта категория
    ai_is_info_page BOOLEAN DEFAULT FALSE,  -- кандидат на роль главной ИНФО-группы региона

    -- Moderation
    status VARCHAR(20) NOT NULL DEFAULT 'pending',  -- pending/approved/rejected/deferred

    -- Source (для дебага: каким запросом нашли)
    discovered_via VARCHAR(80),             -- 'geo_search', 'kw:novosti', 'kw:dtp', 'reposts_of_main'

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_candidates_region_vk
    ON community_candidates(region_id, vk_id);

CREATE INDEX IF NOT EXISTS idx_candidates_status_region
    ON community_candidates(status, region_id);

-- Триггер updated_at (использует общую функцию из миграции 003, если есть;
-- иначе создаём здесь).
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE 'plpgsql';

DROP TRIGGER IF EXISTS update_community_candidates_updated_at
    ON community_candidates;
CREATE TRIGGER update_community_candidates_updated_at
    BEFORE UPDATE ON community_candidates
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
