-- 031: сообщества — ежедневные снимки числа подписчиков (фундамент графика роста).
--
-- Контекст. У `communities` нет ни `members_count`, ни истории — строить график
-- роста подписчиков (owner-request 2026-06-05) не из чего. Эта таблица копит
-- дневные снимки: суточная beat-таска (`collect_member_snapshots`) тянет
-- `groups.getById(fields=members_count)` батчами по активным сообществам и
-- пишет по строке на (сообщество, день). Через несколько недель снимков —
-- мульти-line Chart.js по выбранным сообществам.
--
-- community_member_snapshots — иммутабельные дневные снимки:
--   * community_id  — FK на communities (ON DELETE CASCADE — снимки уходят с группой);
--   * members_count — число подписчиков на момент снимка;
--   * snapshot_date — дата снимка (DATE, ключ группировки по дням);
--   * created_at    — когда записано (аудит/отладка).
--
-- Уникальный индекс (community_id, snapshot_date) — один снимок на день; повторный
-- прогон таски за тот же день идемпотентен (ON CONFLICT DO UPDATE перезапишет count).
-- Идемпотентна: CREATE TABLE/INDEX IF NOT EXISTS, GRANT повторно no-op.

CREATE TABLE IF NOT EXISTS community_member_snapshots (
    id            BIGSERIAL PRIMARY KEY,
    community_id  INTEGER NOT NULL REFERENCES communities(id) ON DELETE CASCADE,
    members_count INTEGER NOT NULL,
    snapshot_date DATE NOT NULL,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_member_snapshot_community_day
    ON community_member_snapshots(community_id, snapshot_date);

-- Запросы графика: «снимки сообщества X по дате» — ведущий столбец community_id уже
-- в уникальном индексе, отдельный по дате помогает обрезке окна по всем сообществам.
CREATE INDEX IF NOT EXISTS idx_member_snapshot_date
    ON community_member_snapshots(snapshot_date);

GRANT ALL PRIVILEGES ON TABLE community_member_snapshots TO setka_user;
GRANT USAGE, SELECT ON SEQUENCE community_member_snapshots_id_seq TO setka_user;
