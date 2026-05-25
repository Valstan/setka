-- 013: regions.last_discovery_at
--
-- Timestamp последнего успешного запуска discovery для региона. Используется:
--   * UI /regions: показывает «когда последний раз искали новые сообщества»
--     рядом с кнопкой «Найти новые сообщества»;
--   * (отдельный PR) Celery beat-таска для авто-ротации discovery по
--     активным регионам — выбирает регион с самым старым last_discovery_at.
--
-- NULL означает «discovery никогда не запускался» (для старых регионов до
-- 2026-05-26 либо для новых черновиков).
--
-- Идемпотентна: повторное применение — no-op.

ALTER TABLE regions
    ADD COLUMN IF NOT EXISTS last_discovery_at TIMESTAMP NULL;
