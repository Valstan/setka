-- 054: HITL-классификатор — перенаправление на реальный источник (ADR-0003, правка).
--
-- Находка при деплое 2026-07-05: таблица posts ПУСТА — активный конвейер SARAFAN
-- не пишет пер-пост Post-строки, а копит кандидатов внутри свод­ок
-- (bulletin_curation_runs.candidates JSONB: {lip, url, text, post_id, owner_id,
-- has_media}). Живой источник — именно он (26 свод­ок за 2 дня). Ключ идентичности
-- поста — lip ("<owner_abs>_<post_id>", структурный фингерпринт, стабилен).
--
-- Поэтому classification-таблицы переключаем с post_id (FK на пустую posts) на
-- lip + снапшот текста/url (кандидат в свод­ке транзиентен). Таблицы миграции 053
-- пустые (только что созданы) → безопасно пересоздать.
--
-- Идемпотентно. Откат: DROP этих + применить 053 обратно.

DROP TABLE IF EXISTS classification_corrections;
DROP TABLE IF EXISTS content_classifications;

CREATE TABLE content_classifications (
    id BIGSERIAL PRIMARY KEY,
    -- структурный ключ поста: "<owner_abs>_<post_id>" (create_lip_fingerprint)
    lip VARCHAR(50) NOT NULL,
    region_code VARCHAR(50) NOT NULL,
    -- снапшот контента на момент классификации (кандидат в свод­ке транзиентен)
    post_text TEXT NULL,
    post_url VARCHAR(300) NULL,
    source VARCHAR(20) NOT NULL DEFAULT 'routine',  -- routine | api
    model VARCHAR(50) NULL,
    verdict JSONB NOT NULL,  -- ADR-0003 §B: theme/action/merge_with(lips)/split/confidence/reasoning
    confidence INTEGER NULL,
    shadow BOOLEAN NOT NULL DEFAULT TRUE,
    escalated BOOLEAN NOT NULL DEFAULT FALSE,
    tokens_estimate INTEGER NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX uq_content_classifications_lip ON content_classifications (lip);
CREATE INDEX ix_content_classifications_region ON content_classifications (region_code);
CREATE INDEX ix_content_classifications_created ON content_classifications (created_at);

CREATE TABLE classification_corrections (
    id BIGSERIAL PRIMARY KEY,
    classification_id BIGINT NOT NULL
        REFERENCES content_classifications(id) ON DELETE CASCADE,
    lip VARCHAR(50) NOT NULL,
    verdict_type VARCHAR(20) NOT NULL,          -- theme | action | merge
    outcome VARCHAR(10) NOT NULL DEFAULT 'correct',  -- agree | correct
    ai_value JSONB NULL,
    operator_value JSONB NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX ix_classification_corrections_cls ON classification_corrections (classification_id);
CREATE INDEX ix_classification_corrections_type_outcome
    ON classification_corrections (verdict_type, outcome);
