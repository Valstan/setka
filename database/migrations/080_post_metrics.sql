-- 080: метрики поста и дата публикации в аудит сбора — звено 5, шаг 1.
--
-- Целевая механика звена 5 (формулировка владельца 2026-08-19): мешок
-- допущенных → отсев по старости 72 часа → выбор под тему из расписания →
-- рейтинг по популярности → с верхушки набирается один пост в корневую
-- группу. Ни рейтинга, ни данных под него в базе не было вовсе: в
-- collected_post_audit нет ни метрик, ни даты самого поста.
--
-- published_at — дата поста В ВК (поле date из API), а НЕ момент нашей
-- публикации. 72 часа отсчитываются как возраст поста; то, что мы уже
-- опубликовали, отслеживается отдельно, в work_tables.lip.
--
-- ВСЕ колонки NULL-able и БЕЗ дефолта 0 — сознательно. NULL значит «не
-- мерили», 0 — «ноль реакций». Дефолт 0 соврал бы ровно так, как соврала
-- панель классификатора в #493: tokens_estimate был пуст у 2238 вердиктов
-- из 2238, а рисовалось «токенов: 0» там, где ушло ~1.4 млн токенов.
--
-- Backfill не нужен: published_at для новых строк пишет сам сбор (date уже
-- лежит в словаре поста), а для существующих её заполнит таска обновления
-- метрик — wall.getById возвращает date попутно.

ALTER TABLE collected_post_audit
    ADD COLUMN IF NOT EXISTS published_at       TIMESTAMP NULL,
    ADD COLUMN IF NOT EXISTS views              INTEGER   NULL,
    ADD COLUMN IF NOT EXISTS likes              INTEGER   NULL,
    ADD COLUMN IF NOT EXISTS comments           INTEGER   NULL,
    ADD COLUMN IF NOT EXISTS reposts            INTEGER   NULL,
    ADD COLUMN IF NOT EXISTS metrics_updated_at TIMESTAMP NULL;

-- Окно отбора — «посты моложе 72 часов». Индекс под него.
CREATE INDEX IF NOT EXISTS ix_collected_post_audit_published_at
    ON collected_post_audit (published_at);

-- Откат:
-- DROP INDEX IF EXISTS ix_collected_post_audit_published_at;
-- ALTER TABLE collected_post_audit
--     DROP COLUMN IF EXISTS published_at,
--     DROP COLUMN IF EXISTS views,
--     DROP COLUMN IF EXISTS likes,
--     DROP COLUMN IF EXISTS comments,
--     DROP COLUMN IF EXISTS reposts,
--     DROP COLUMN IF EXISTS metrics_updated_at;
