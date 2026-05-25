-- 012: community_candidates.ai_is_relevant
--
-- Расширение модели candidate под human-in-the-loop AI-категоризацию (через
-- clipboard, без Groq API). Колонка хранит явный ответ нейросети «является
-- ли это сообщество принадлежащим географически данному району».
--
-- Тройная логика:
--   NULL   — ещё не оценено (новый candidate, AI batch не прогонялся).
--   TRUE   — нейросеть/модератор подтвердили геопринадлежность.
--   FALSE  — нейросеть/модератор отметили нерелевантным; UI прячет
--             по дефолту (фильтр «только релевантные»).
--
-- Отдельно от ``ai_confidence`` намеренно: confidence — про уверенность в
-- категории (novost/sport/...), is_relevant — про геопринадлежность.
-- Сообщество может быть «уверенно novost» и «явно нерелевантно району».
--
-- Идемпотентна: повторное применение — no-op.

ALTER TABLE community_candidates
    ADD COLUMN IF NOT EXISTS ai_is_relevant BOOLEAN DEFAULT NULL;

-- Index для фильтра «только релевантные» в UI. Partial — не индексируем NULL/FALSE
-- (NULL — большинство до AI batch'а; FALSE — модератор всё равно скрывает).
CREATE INDEX IF NOT EXISTS idx_candidates_relevant
    ON community_candidates(region_id, status)
    WHERE ai_is_relevant IS TRUE;
