-- 057: classifier learning loop — выученные правила (ADR-0005).
--
-- Замыкает петлю обучения: коррекции оператора в ленте /classifier →
-- дистилляция (облачная рутина) чеканит ЧЕРНОВИКИ обобщённых правил → оператор
-- утверждает/правит/отклоняет в вебе → утверждённые правила подмешиваются в
-- эффективные постулаты (base-файл в git + этот overlay), которые рутина читает
-- каждый прогон. Нейросеть НИКОГДА не правит правила сама — только предлагает;
-- применяет человек. Родня deny-лог pool #054 («лог реальности → правила»).
--
-- Divergence от ADR-0003 §E (правила только в git-файле): выученный слой живёт в
-- БД ради ЖИВОГО веб-утверждения (утвердил кнопкой → правило сразу в деле, без
-- git-коммита). Базовый config/classification_postulates.md остаётся в git;
-- периодический снапшот утверждённых правил в файл — для аудита/отката (ADR-0005).
--
-- Идемпотентно. Откат: DROP TABLE classification_rules;

CREATE TABLE IF NOT EXISTS classification_rules (
    id BIGSERIAL PRIMARY KEY,
    -- NULL = глобальное правило (все районы); код района = только для него
    region_code VARCHAR(50) NULL,
    rule_text TEXT NOT NULL,
    -- proposed (черновик рутины/оператора) | approved (в деле) | rejected | retired
    status VARCHAR(12) NOT NULL DEFAULT 'proposed',
    -- кто предложил: routine (дистилляция) | operator (руками)
    source VARCHAR(12) NOT NULL DEFAULT 'routine',
    -- почему рутина предложила (1 строка) + доказательная база (какие коррекции)
    rationale TEXT NULL,
    evidence JSONB NULL,
    model VARCHAR(50) NULL,
    -- нормализованный rule_text для дедупа предложений (не уникальный индекс —
    -- дедуп в коде только против активных proposed|approved, см. rules.py)
    norm_key VARCHAR(200) NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    -- когда оператор решил (approve/reject/retire)
    decided_at TIMESTAMP NULL,
    -- когда правило в последний раз подавалось в эффективные постулаты (aging #033)
    last_effective_at TIMESTAMP NULL
);

CREATE INDEX IF NOT EXISTS ix_classification_rules_status ON classification_rules (status);
CREATE INDEX IF NOT EXISTS ix_classification_rules_region ON classification_rules (region_code);
CREATE INDEX IF NOT EXISTS ix_classification_rules_norm ON classification_rules (norm_key);
