-- 034: еженедельный снимок УНИКАЛЬНЫХ подписчиков области (без дублей).
--
-- Контекст (owner-request 2026-06-07). График роста (миграция 033) сравнивает
-- главные ИНФО-группы регионов и умеет суммировать их по области («Σ область»).
-- Но сумма завышена: человек, подписанный на 3 районные группы области, учтён
-- трижды. Чтобы сравнивать ОБЛАСТИ по «чистому» охвату, нужен union member-id
-- всех главных групп области (сама область + районы, parent_region_id=oblast.id)
-- через groups.getMembers.
--
-- groups.getMembers по ~16 главным группам дёшев (1000 id/запрос) → копим
-- еженедельно ночью (медленная метрика, ежедневно не нужно). Иммутабельные
-- снимки:
--   * oblast_region_id  — FK на regions (область, kind='oblast'); CASCADE;
--   * unique_count      — мощность объединения множеств member-id групп области;
--   * total_with_dupes  — сумма |members| по группам (для коэффициента дублей);
--   * group_count       — сколько групп реально вошло (закрытые/ошибки пропущены);
--   * snapshot_date     — дата снимка (DATE, ключ группировки);
--   * created_at        — когда записано (аудит).
--
-- Уникальный индекс (oblast_region_id, snapshot_date) — один снимок на день;
-- повторный прогон за день идемпотентен (ON CONFLICT DO UPDATE).
-- Идемпотентна: CREATE ... IF NOT EXISTS, GRANT повторно no-op.

CREATE TABLE IF NOT EXISTS oblast_unique_member_snapshots (
    id               BIGSERIAL PRIMARY KEY,
    oblast_region_id INTEGER NOT NULL REFERENCES regions(id) ON DELETE CASCADE,
    unique_count     INTEGER NOT NULL,
    total_with_dupes INTEGER NOT NULL,
    group_count      INTEGER NOT NULL,
    snapshot_date    DATE NOT NULL,
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_oblast_unique_member_snapshot_day
    ON oblast_unique_member_snapshots(oblast_region_id, snapshot_date);

-- Обрезка окна по всем областям сразу (запрос /regions, /series) — индекс по дате.
CREATE INDEX IF NOT EXISTS idx_oblast_unique_member_snapshot_date
    ON oblast_unique_member_snapshots(snapshot_date);

GRANT ALL PRIVILEGES ON TABLE oblast_unique_member_snapshots TO setka_user;
GRANT USAGE, SELECT ON SEQUENCE oblast_unique_member_snapshots_id_seq TO setka_user;
