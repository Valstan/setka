-- 035: shadow-журнал LLM-курации дайджестов (PoC, Фаза 1 — письмо brain 2026-06-07
-- «LLM-курация дайджестов», compliance=suggest).
--
-- Контекст. Текущий конвейер дайджеста синхронный: beat-волна → сбор постов →
-- детерминированные фильтры (реклама/религия/дедуп/relevance) → DigestBuilder →
-- VKPublisher.publish_digest. Премиса brain'а: алгоритмы пропускают мусор
-- (перефразированные дубли, тонкую нерелевантность району). Прежде чем отдавать
-- LLM управление публикацией (enforcing), мерим её качество в SHADOW-режиме:
-- публикуем как и сейчас, но параллельно паркуем УЖЕ ОПУБЛИКОВАННЫЕ посты
-- дайджеста в этот журнал. Slash-команда /curate (Claude Code /loop) читает
-- pending-прогоны, по рубрике релевантности ставит per-post вердикт keep/drop +
-- причину. Разница «сколько LLM бы отсеяла» = дельта над текущим алгоритмом
-- (каждый кандидат тут уже прошёл все фильтры). Нулевой риск публикации, нулевой
-- сдвиг тайминга, fail-open by design (сбой recorder'а изолирован, см.
-- modules/curation/recorder.py — отдельная сессия + best-effort try/except).
--
-- Гранулярность — per-post внутри одного прогона (один прогон = одна публикация
-- дайджеста). candidates/verdicts — JSONB-массивы, без partial-index UPSERT, так
-- что граблю G40 (литерал в index_where) не трогаем: append-only журнал прогонов,
-- /curate апдейтит verdicts строки по id (идемпотентно — повторный apply
-- перезаписывает вердикты и reviewed_at).
--
-- Идемпотентна: CREATE ... IF NOT EXISTS, GRANT повторно no-op.

CREATE TABLE IF NOT EXISTS digest_curation_runs (
    id                BIGSERIAL PRIMARY KEY,
    region_code       VARCHAR(50)  NOT NULL,
    theme             VARCHAR(50)  NOT NULL,
    kind              VARCHAR(20)  NOT NULL DEFAULT 'regular',  -- regular|mourning|neighbors
    status            VARCHAR(20)  NOT NULL DEFAULT 'pending',  -- pending|reviewed
    shadow            BOOLEAN      NOT NULL DEFAULT TRUE,       -- TRUE = опубликовано как обычно (Фаза 1)

    -- Посты, реально вошедшие в опубликованный дайджест (digest.posts_included):
    --   [{lip, owner_id, post_id, text, has_media, url}]
    candidates        JSONB        NOT NULL,
    total_count       INTEGER      NOT NULL,

    -- Заполняется /curate: [{lip, verdict: 'keep'|'drop', reason}]
    verdicts          JSONB,
    flagged_count     INTEGER,                                 -- сколько drop (дельта над алгоритмом)
    tokens_estimate   INTEGER,                                 -- грубая оценка токенов прогона (token-economy)

    -- Что фактически опубликовано текущим путём (для сверки/трассировки):
    published_post_id BIGINT,
    published_url     VARCHAR(500),

    created_at        TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reviewed_at       TIMESTAMP
);

-- /curate выбирает pending по статусу; дашборд/отчёт — по дате.
CREATE INDEX IF NOT EXISTS idx_digest_curation_runs_status  ON digest_curation_runs(status);
CREATE INDEX IF NOT EXISTS idx_digest_curation_runs_created ON digest_curation_runs(created_at);

GRANT ALL PRIVILEGES ON TABLE digest_curation_runs TO setka_user;
GRANT USAGE, SELECT ON SEQUENCE digest_curation_runs_id_seq TO setka_user;
