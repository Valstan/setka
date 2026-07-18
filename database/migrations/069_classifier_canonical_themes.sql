-- 069: canonical theme dictionary for the HITL classifier (owner order 2026-07-18).
-- Free-form themes produced ~180 variants in two weeks (ru/en duplicates, typos).
-- This creates the dictionary table and seeds the 12 canonical Russian themes.
-- Data normalization of existing verdicts is a SEPARATE gated step (see PR).

BEGIN;

CREATE TABLE IF NOT EXISTS classifier_themes (
    id BIGSERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL UNIQUE,
    position INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT now()
);

INSERT INTO classifier_themes (name, position) VALUES
    ('новости', 1),
    ('происшествия', 2),
    ('объявления', 3),
    ('администрация', 4),
    ('культура', 5),
    ('спорт', 6),
    ('образование', 7),
    ('детский сад', 8),
    ('православие', 9),
    ('научпоп', 10),
    ('соседи', 11),
    ('мусор', 12)
ON CONFLICT (name) DO NOTHING;

COMMIT;

-- Rollback: DROP TABLE classifier_themes;
