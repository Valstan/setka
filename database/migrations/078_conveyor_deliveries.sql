-- 078: журнал доставок контент-конвейера ВК → сайты кластера (D-015, MANDATE brain 2026-08-08).
--
-- Одна строка = «этот пост для этого сайта» на всём пути: отобран → расклассифицирован
-- LLM → доставлен / отклонён / задержан. Нужна по трём причинам, и ни одну не
-- закрывает идемпотентность на стороне сайта:
--
--   1. ОТБОР. Без журнала следующий прогон снова возьмёт те же посты и снова
--      заплатит за LLM-вызовы. Дедуп сайта по vkPostId спасает его коллекцию,
--      но не наш счёт за токены.
--   2. МЕТРИКА. Автопубликация включается по agree-rate КОНКРЕТНОГО сайта
--      (§D shadow-first) — считать её не из чего, если решения не хранятся.
--   3. РАЗБОР. «Почему этой новости нет на сайте» — вопрос, который будет
--      задан. Ответ должен быть строкой в журнале (отклонён LLM / задержан
--      инвариантом / упал на доставке), а не молчанием.
--
-- Ключ идентичности — (site, lip), а не lip: один пост может уйти на два сайта
-- (район и ДК), и это разные доставки со своими вердиктами.
--
-- Идемпотентно. Откат: DROP TABLE conveyor_deliveries;

CREATE TABLE IF NOT EXISTS conveyor_deliveries (
    id BIGSERIAL PRIMARY KEY,
    -- ключ сайта из config/content_conveyor.py::SITES
    site VARCHAR(50) NOT NULL,
    -- структурный ключ поста "<owner_abs>_<post_id>" — тот же, что в
    -- collected_post_audit и content_classifications
    lip VARCHAR(50) NOT NULL,
    -- selected  — отобран, ждёт LLM
    -- classified— есть вердикт, ждёт доставки
    -- delivered — принят сайтом (draft)
    -- rejected  — LLM сказал «не для этого сайта»
    -- held      — задержан инвариантом мусора (pool #133) ЛИБО оператором
    -- failed    — доставка не удалась после ретраев
    status VARCHAR(16) NOT NULL DEFAULT 'selected',
    -- почему rejected/held/failed — свободный короткий код для разбора
    reason VARCHAR(64) NULL,
    -- вердикт LLM целиком: {rubric, title, text, merge_with?, confidence}
    verdict JSONB NULL,
    -- сколько раз пытались доставить (ретраи на 502/503/504, G234)
    attempts INTEGER NOT NULL DEFAULT 0,
    http_status INTEGER NULL,
    -- id материала на стороне сайта, если он его вернул — чтобы связать
    -- нашу строку с draft'ом в админке без поиска по тексту
    remote_id VARCHAR(100) NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Идемпотентность отбора: повторный прогон не создаёт вторую строку.
CREATE UNIQUE INDEX IF NOT EXISTS uq_conveyor_deliveries_site_lip
    ON conveyor_deliveries (site, lip);
-- Рабочий индекс прогона: «что на этом сайте ждёт следующего шага».
CREATE INDEX IF NOT EXISTS ix_conveyor_deliveries_site_status
    ON conveyor_deliveries (site, status);
CREATE INDEX IF NOT EXISTS ix_conveyor_deliveries_created
    ON conveyor_deliveries (created_at);
