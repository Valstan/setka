-- 056: collection audit — классификатор видит обе стороны сбора (ADR-0004).
--
-- Проблема (найдено 2026-07-07): классификатор читал bulletin_curation_runs.candidates
-- = только ОПУБЛИКОВАННЫЕ посты (recorder паркует posts_included) → слеп к
-- пере-фильтрации (хорошие посты, выброшенные фильтром зря, нигде не хранились).
--
-- Решение (вариант B владельца): новый shadow-журнал каждого собранного поста с
-- решением фильтра — kept | dropped(причина). Классификатор читает его → видит
-- обе стороны. Захват — fail-safe рекордер на границе сбора (никогда не ломает
-- публикацию), причина отсева пере-выводится теми же чистыми функциями.
--
-- Идемпотентно. Откат: DROP TABLE collected_post_audit;

CREATE TABLE IF NOT EXISTS collected_post_audit (
    id BIGSERIAL PRIMARY KEY,
    -- структурный ключ поста: "<owner_abs>_<post_id>" (lip_of_post), стабилен,
    -- совпадает с ключом content_classifications → классификатор джойнит по нему
    lip VARCHAR(50) NOT NULL,
    region_code VARCHAR(50) NOT NULL,
    theme VARCHAR(50) NULL,
    -- снапшот контента на момент сбора (пост в ВК транзиентен)
    post_text TEXT NULL,
    post_url VARCHAR(300) NULL,
    has_media BOOLEAN NOT NULL DEFAULT FALSE,
    -- решение детерминированного фильтра
    decision VARCHAR(12) NOT NULL,       -- kept | dropped
    -- причина отсева (NULL для kept): advertisement | blacklist_text |
    -- no_region_words | no_attachments (механические дропы — возраст/дедуп — НЕ пишем)
    drop_reason VARCHAR(32) NULL,
    collected_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Идемпотентность по lip (first-seen wins): пост, увиденный повторно, пропускается.
CREATE UNIQUE INDEX IF NOT EXISTS uq_collected_post_audit_lip ON collected_post_audit (lip);
CREATE INDEX IF NOT EXISTS ix_collected_post_audit_region ON collected_post_audit (region_code);
CREATE INDEX IF NOT EXISTS ix_collected_post_audit_collected ON collected_post_audit (collected_at);
CREATE INDEX IF NOT EXISTS ix_collected_post_audit_decision ON collected_post_audit (decision);
