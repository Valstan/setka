-- 053: HITL-классификатор контента — shadow-таблицы (ADR-0003).
--
-- Вариант B (решение владельца 2026-07-05): классификацию на этапе shadow
-- делает облачная рутина через HTTP-интерфейс; таблицы одинаковы для рутины
-- и будущего Claude-API-пути (Celery-таск).
--
-- content_classifications — пер-пост вердикт нейронки (только пишем в shadow,
--   Post не трогаем). classification_corrections — лог несогласий оператора
--   (сырьё для agree-rate по-типам + дистилляции в файл-корректировщик).
--
-- Аддитивно и идемпотентно. Откат:
--   DROP TABLE IF EXISTS classification_corrections, content_classifications;

CREATE TABLE IF NOT EXISTS content_classifications (
    id BIGSERIAL PRIMARY KEY,
    post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    region_code VARCHAR(50) NOT NULL,
    -- источник вердикта: routine (облачная рутина, этап B) | api (Claude API из Celery)
    source VARCHAR(20) NOT NULL DEFAULT 'routine',
    -- модель, выдавшая вердикт (haiku / opus / routine-<model>); свободная строка
    model VARCHAR(50) NULL,
    -- вердикт целиком (схема ADR-0003 §B): theme/action/merge_with/split/confidence/reasoning
    verdict JSONB NOT NULL,
    confidence INTEGER NULL,          -- дубль из verdict для быстрых выборок/сортировки
    shadow BOOLEAN NOT NULL DEFAULT TRUE,
    escalated BOOLEAN NOT NULL DEFAULT FALSE,  -- был ли эскалирован Haiku->Opus
    tokens_estimate INTEGER NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Один активный вердикт на пост (в shadow переклассификации нет: /pending
-- отдаёт только посты без вердикта). Частичный unique — на будущее, если
-- добавим переклассификацию, снимем.
CREATE UNIQUE INDEX IF NOT EXISTS uq_content_classifications_post
    ON content_classifications (post_id);
CREATE INDEX IF NOT EXISTS ix_content_classifications_region
    ON content_classifications (region_code);
CREATE INDEX IF NOT EXISTS ix_content_classifications_created
    ON content_classifications (created_at);

CREATE TABLE IF NOT EXISTS classification_corrections (
    id BIGSERIAL PRIMARY KEY,
    classification_id BIGINT NOT NULL
        REFERENCES content_classifications(id) ON DELETE CASCADE,
    post_id INTEGER NOT NULL,
    -- какой аспект вердикта поправили: theme | action | merge  (agree — отдельно, ниже)
    verdict_type VARCHAR(20) NOT NULL,
    -- 'agree' | 'correct' — согласие оператора тоже строка в этом же логе,
    -- чтобы agree-rate = agrees / (agrees + corrects) по verdict_type считался одним запросом
    outcome VARCHAR(10) NOT NULL DEFAULT 'correct',
    ai_value JSONB NULL,        -- что предложила нейронка (для correct)
    operator_value JSONB NULL,  -- что поставил оператор (для correct)
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_classification_corrections_cls
    ON classification_corrections (classification_id);
CREATE INDEX IF NOT EXISTS ix_classification_corrections_type_outcome
    ON classification_corrections (verdict_type, outcome);
