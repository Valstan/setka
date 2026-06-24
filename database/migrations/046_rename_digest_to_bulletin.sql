-- 046_rename_digest_to_bulletin.sql
--
-- Переименование «digest» → «bulletin» в прод-схеме/данных (PR-4 серии
-- терминологии 2026-06-24). Видимый текст (посты/UI) и код уже переведены
-- (PR #271/#272/#273); здесь — внутренние контракты БД:
--   1) таблица   digest_curation_runs        → bulletin_curation_runs
--   2) колонка   region_configs.digest_filters → bulletin_filters
--   3) JSON-ключ region_configs.bulletin_filters: nested max_posts_per_digest
--                                              → max_posts_per_bulletin
--   4) JSON-ключ regions.config: digest_template → bulletin_template
--
-- НЕ трогаем (решение владельца): метрики Prometheus (setka_digest_*),
-- Redis-ключ heartbeat (setka:digest_last_published). Имена Celery-задач
-- меняются в коде+beat (не в БД).
--
-- Все шаги идемпотентны (guard'ы по information_schema / наличию ключа), чтобы
-- повторный прогон через scripts/migrate.py был безопасен. json-колонки кастуем
-- к jsonb для манипуляций и обратно к json при записи.

-- 1) RENAME TABLE (метаданные, данные сохраняются)
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_name = 'digest_curation_runs'
    ) AND NOT EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_name = 'bulletin_curation_runs'
    ) THEN
        ALTER TABLE digest_curation_runs RENAME TO bulletin_curation_runs;
    END IF;
END $$;

-- 2) RENAME COLUMN region_configs.digest_filters → bulletin_filters
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'region_configs' AND column_name = 'digest_filters'
    ) AND NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'region_configs' AND column_name = 'bulletin_filters'
    ) THEN
        ALTER TABLE region_configs RENAME COLUMN digest_filters TO bulletin_filters;
    END IF;
END $$;

-- 3) nested key max_posts_per_digest → max_posts_per_bulletin внутри
--    bulletin_filters (в defaults и в каждом by_topic.*). Идемпотентно.
DO $$
DECLARE
    r RECORD;
    bf jsonb;
    defs jsonb;
    newbt jsonb;
    k text;
    topic_obj jsonb;
BEGIN
    FOR r IN
        SELECT id, bulletin_filters
        FROM region_configs
        WHERE bulletin_filters IS NOT NULL
    LOOP
        bf := r.bulletin_filters::jsonb;

        IF bf #> '{defaults,max_posts_per_digest}' IS NOT NULL THEN
            defs := bf->'defaults';
            defs := (defs - 'max_posts_per_digest')
                || jsonb_build_object('max_posts_per_bulletin', defs->'max_posts_per_digest');
            bf := jsonb_set(bf, '{defaults}', defs);
        END IF;

        IF bf ? 'by_topic' THEN
            newbt := '{}'::jsonb;
            FOR k IN SELECT jsonb_object_keys(bf->'by_topic')
            LOOP
                topic_obj := bf #> ARRAY['by_topic', k];
                IF topic_obj ? 'max_posts_per_digest' THEN
                    topic_obj := (topic_obj - 'max_posts_per_digest')
                        || jsonb_build_object(
                            'max_posts_per_bulletin', topic_obj->'max_posts_per_digest');
                END IF;
                newbt := newbt || jsonb_build_object(k, topic_obj);
            END LOOP;
            bf := jsonb_set(bf, '{by_topic}', newbt);
        END IF;

        UPDATE region_configs SET bulletin_filters = bf::json WHERE id = r.id;
    END LOOP;
END $$;

-- 4) top-level JSON-ключ regions.config: digest_template → bulletin_template
UPDATE regions
SET config = (
        (config::jsonb - 'digest_template')
        || jsonb_build_object('bulletin_template', config::jsonb->'digest_template')
    )::json
WHERE config::jsonb ? 'digest_template';
