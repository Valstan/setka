-- 087: модуль «Раскрутка» — продвижение молодых сообществ сети (заказ владельца 2026-08-28).
--
-- ЗАЧЕМ. 19 районов, запущенных с июля, имеют по 2–7 подписчиков и растут ~1/неделю,
-- пока 11 старых держат ≈29 000. Замер 28.08 показал, что узкое место — не раздача
-- контента (соседский обмен работает ежедневно и роста не даёт: у Верхошижемья семь
-- сильных соседей и 22 подписчика), а НАХОДИМОСТЬ: у 23 из 36 активных районов пусты
-- local_hashtags, у 33 — vk_city_id. Модуль чинит находимость, готовит кандидатов для
-- ручного аутрича и — как эксперимент с контрольной группой — крутит промо-посты.
--
-- СЕМАНТИКА ПОЛЕЙ, которую легко прочитать неверно:
--
-- promo_actions.slot_key — ISO-неделя ('2026-W35') для недельных каналов либо дата
--   ('2026-08-28') для суточных. Вместе с двумя UNIQUE ниже это и есть НАСТОЯЩЕЕ
--   соблюдение квот «1 промо в неделю на донора» и «1 в неделю на цель»: уникум держится
--   при гонке beat, при рестарте mid-run и при упавшем Redis, а Redis — только быстрый
--   отказ. Диспетчер клеймит строку через INSERT ... ON CONFLICT DO NOTHING ПЕРЕД
--   публикацией (паттерн broadcast_publications, миграция 044).
-- promo_actions.donor_group_id NULL — у канала setup донора нет. В UNIQUE Postgres
--   считает NULL различными, поэтому для setup роль защёлки берёт второй уникум
--   (channel, target_region_id, slot_key). Оба индекса нужны, ни один не лишний.
-- promo_actions.dry_run DEFAULT TRUE — канал, включённый по недосмотру, ничего не
--   опубликует: он запишет в body текст, который ушёл бы, и остановится.
-- promo_enrollments.members_at_enroll NULL — ЛЕГАЛЬНОЕ состояние, а не ошибка: Суна,
--   Кумёны и Зуевка активированы 27.08 и суточного снимка ещё не имеют. Район без
--   снимка зачисляется (нет данных ≠ «много подписчиков»).
-- promo_settings: вход в раскрутку < threshold_members, выход >= graduate_members.
--   Пороги РАЗНЫЕ намеренно — гистерезис; при равных район мигает вход/выход на границе.
-- promo_group_setup.setup_version — версия шаблона оформления. Повтор той же версии
--   no-op по уникуму; правка шаблона = бамп версии = оформление переприменяется ровно
--   один раз. before/after хранят снимок настроек группы до и после — без них откат
--   невозможен, а канал перезаписывает описание, которое владелец мог писать руками.
-- promo_donor_blacklist.until NULL — забанен навсегда (ручной бан владельца);
--   непустой until ставит автоматика по кодам VK (214/220 → 24ч, 219 → 7 суток).
--
-- Локальные хэштеги (канал A) здесь НЕ проставляются намеренно: разовый UPDATE — это
-- ровно та ошибка, которую мы чиним (13 районов настроены захардкоженным списком в
-- modules/region_config.py, а 23 новых туда не попали). Заполнение делает
-- modules/promotion/hashtags.py на каждом прогоне зачисления — самозалечивающимся.
--
-- Идемпотентна (IF NOT EXISTS / ON CONFLICT DO NOTHING). Применять с ON_ERROR_STOP=1.

BEGIN;

-- Зачисление района в раскрутку. Pull-модель: диспетчер сам добирает активные районы,
-- скрипт активации регионов не трогаем (кэша активных регионов в процессах нет).
CREATE TABLE IF NOT EXISTS promo_enrollments (
    id BIGSERIAL PRIMARY KEY,
    region_id INTEGER NOT NULL REFERENCES regions(id) ON DELETE CASCADE,
    status VARCHAR(20) NOT NULL DEFAULT 'active',      -- active|graduated|paused
    cohort VARCHAR(20) NOT NULL DEFAULT 'pending',     -- pending|wave_a|wave_b|wave_c|control
    members_at_enroll INTEGER,
    members_at_graduate INTEGER,
    reason TEXT,
    enrolled_at TIMESTAMP DEFAULT (now() AT TIME ZONE 'utc'),
    graduated_at TIMESTAMP,
    updated_at TIMESTAMP DEFAULT (now() AT TIME ZONE 'utc')
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_promo_enrollment_region
    ON promo_enrollments(region_id);
CREATE INDEX IF NOT EXISTS ix_promo_enrollments_status
    ON promo_enrollments(status, cohort);

-- План и факт одного действия раскрутки. Строка заводится ДО обращения к VK.
CREATE TABLE IF NOT EXISTS promo_actions (
    id BIGSERIAL PRIMARY KEY,
    channel VARCHAR(20) NOT NULL,          -- hashtags|city|setup|pin|footer|promo_post|oblast_digest|outreach
    donor_group_id BIGINT,
    donor_region_id INTEGER REFERENCES regions(id) ON DELETE SET NULL,
    target_region_id INTEGER NOT NULL REFERENCES regions(id) ON DELETE CASCADE,
    hop SMALLINT NOT NULL DEFAULT 0,       -- 0 = донора нет, 1 сосед, 2 сосед соседа, 3 область
    slot_key VARCHAR(16) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',  -- pending|published|error|skipped|dry_run
    dry_run BOOLEAN NOT NULL DEFAULT TRUE,
    body TEXT,
    vk_method VARCHAR(40),
    vk_post_id BIGINT,
    post_url VARCHAR(300),
    vk_error_code INTEGER,
    error TEXT,
    token_name VARCHAR(50),
    api_calls SMALLINT NOT NULL DEFAULT 0,
    planned_at TIMESTAMP DEFAULT (now() AT TIME ZONE 'utc'),
    published_at TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_promo_action_donor_slot
    ON promo_actions(channel, donor_group_id, slot_key);
CREATE UNIQUE INDEX IF NOT EXISTS uq_promo_action_target_slot
    ON promo_actions(channel, target_region_id, slot_key);
CREATE INDEX IF NOT EXISTS ix_promo_actions_status
    ON promo_actions(status, planned_at);
CREATE INDEX IF NOT EXISTS ix_promo_actions_target
    ON promo_actions(target_region_id, published_at);

-- Настройки владельца. Одна строка (id=1) — это не таблица-справочник.
CREATE TABLE IF NOT EXISTS promo_settings (
    id SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    threshold_members INTEGER NOT NULL DEFAULT 300,
    graduate_members INTEGER NOT NULL DEFAULT 400,
    donor_min_members INTEGER NOT NULL DEFAULT 1000,
    max_per_donor_per_week SMALLINT NOT NULL DEFAULT 1,
    max_per_target_per_week SMALLINT NOT NULL DEFAULT 1,
    max_actions_per_day SMALLINT NOT NULL DEFAULT 3,
    quiet_hours_start SMALLINT NOT NULL DEFAULT 19,
    quiet_hours_end SMALLINT NOT NULL DEFAULT 10,
    second_hop_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    oblast_fallback_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    oblast_group_id BIGINT DEFAULT -168170001,
    channels JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMP DEFAULT (now() AT TIME ZONE 'utc')
);

INSERT INTO promo_settings (id) VALUES (1) ON CONFLICT DO NOTHING;

-- Доноры, которым сейчас нельзя отдавать промо.
CREATE TABLE IF NOT EXISTS promo_donor_blacklist (
    id BIGSERIAL PRIMARY KEY,
    donor_group_id BIGINT NOT NULL,
    reason TEXT NOT NULL,
    until TIMESTAMP,
    created_at TIMESTAMP DEFAULT (now() AT TIME ZONE 'utc')
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_promo_blacklist_donor
    ON promo_donor_blacklist(donor_group_id);

-- Журнал автооформления со снимком «до» — без него откат невозможен.
CREATE TABLE IF NOT EXISTS promo_group_setup (
    id BIGSERIAL PRIMARY KEY,
    region_id INTEGER NOT NULL REFERENCES regions(id) ON DELETE CASCADE,
    setup_version SMALLINT NOT NULL DEFAULT 1,
    before JSONB,
    after JSONB,
    applied_fields JSONB,
    pinned_post_url VARCHAR(300),
    status VARCHAR(20) NOT NULL DEFAULT 'dry_run',  -- dry_run|applied|error|rolled_back
    vk_error_code INTEGER,
    error TEXT,
    applied_at TIMESTAMP DEFAULT (now() AT TIME ZONE 'utc')
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_promo_setup_region_version
    ON promo_group_setup(region_id, setup_version);

-- Кандидаты ручного аутрича. SETKA их только готовит — отправляет владелец сам.
CREATE TABLE IF NOT EXISTS promo_outreach_candidates (
    id BIGSERIAL PRIMARY KEY,
    target_region_id INTEGER NOT NULL REFERENCES regions(id) ON DELETE CASCADE,
    vk_group_id BIGINT NOT NULL,
    name VARCHAR(300) NOT NULL DEFAULT '',
    screen_name VARCHAR(100),
    members_count INTEGER,
    score REAL,
    draft_text TEXT,
    status VARCHAR(20) NOT NULL DEFAULT 'new',  -- new|contacted|agreed|declined|ignored
    owner_note TEXT,
    found_at TIMESTAMP DEFAULT (now() AT TIME ZONE 'utc'),
    updated_at TIMESTAMP DEFAULT (now() AT TIME ZONE 'utc')
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_promo_outreach_group
    ON promo_outreach_candidates(target_region_id, vk_group_id);
CREATE INDEX IF NOT EXISTS ix_promo_outreach_region_status
    ON promo_outreach_candidates(target_region_id, status);

-- Размер донорских сообществ района — сырьё ранжирования аутрича и оценки «сколько
-- вообще людей в районе можно достать». Один батч groups.getById берёт 500 групп,
-- то есть 4 вызова закрывают всю сеть (1627 строк).
-- NULL-able и БЕЗ дефолта 0 сознательно: NULL значит «не мерили», 0 — «пустая группа».
ALTER TABLE communities ADD COLUMN IF NOT EXISTS members_count INTEGER;
ALTER TABLE communities ADD COLUMN IF NOT EXISTS members_checked_at TIMESTAMP;

COMMIT;

-- Откат:
-- DROP TABLE IF EXISTS promo_outreach_candidates, promo_group_setup,
--     promo_donor_blacklist, promo_settings, promo_actions, promo_enrollments;
-- ALTER TABLE communities DROP COLUMN IF EXISTS members_count,
--     DROP COLUMN IF EXISTS members_checked_at;
