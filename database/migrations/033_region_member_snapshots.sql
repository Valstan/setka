-- 033: snapshots подписчиков по ГЛАВНЫМ ИНФО-группам регионов (а не по всему пулу).
--
-- Контекст. График роста (миграция 031) копил снимки по ВСЕМ активным
-- communities (~840 групп-источников) — это жгло VK API ежедневно и мешало
-- сравнению (нужны только главные группы, куда выпускаем дайджесты). Из 16
-- главных групп регионов в `communities` присутствовала лишь 1 (остальные 15 —
-- только в `regions.vk_group_id`), поэтому фильтром по communities задачу не
-- решить. Переходим на снимки ПО РЕГИОНАМ: одна главная ИНФО-группа на регион.
--
-- Замена per-source → per-region:
--   * DROP старой `community_member_snapshots` (в ней лишь несколько дней
--     throwaway-данных по сообществам, решение владельца 2026-06-07);
--   * region_member_snapshots — иммутабельные дневные снимки:
--       - region_id     — FK на regions (ON DELETE CASCADE — снимки уходят с регионом);
--       - members_count — подписчиков у regions.vk_group_id на момент снимка;
--       - snapshot_date — дата снимка (DATE, ключ группировки по дням);
--       - created_at    — когда записано (аудит).
--
-- Уникальный индекс (region_id, snapshot_date) — один снимок на день; повторный
-- прогон таски за день идемпотентен (ON CONFLICT DO UPDATE перезапишет count).
-- Идемпотентна: DROP/CREATE ... IF [NOT] EXISTS, GRANT повторно no-op.

DROP TABLE IF EXISTS community_member_snapshots;

CREATE TABLE IF NOT EXISTS region_member_snapshots (
    id            BIGSERIAL PRIMARY KEY,
    region_id     INTEGER NOT NULL REFERENCES regions(id) ON DELETE CASCADE,
    members_count INTEGER NOT NULL,
    snapshot_date DATE NOT NULL,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_region_member_snapshot_day
    ON region_member_snapshots(region_id, snapshot_date);

-- Обрезка окна по всем регионам сразу (запрос /regions) — отдельный индекс по дате.
CREATE INDEX IF NOT EXISTS idx_region_member_snapshot_date
    ON region_member_snapshots(snapshot_date);

GRANT ALL PRIVILEGES ON TABLE region_member_snapshots TO setka_user;
GRANT USAGE, SELECT ON SEQUENCE region_member_snapshots_id_seq TO setka_user;
