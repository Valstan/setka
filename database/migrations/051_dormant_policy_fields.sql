-- 051: dormant-политика discovery (одобрена brain 2026-06-30,
-- from-brain/2026-06-30-discovery-dormant-policy-approved.md).
--
-- Поля для видимости soft-disable: когда и почему сообщество выведено из
-- парса. Нужны ежемесячному digest'у вынесенных (условие brain: auto-disable
-- T1 только с digest'ом — окно владельцу возразить, #018).
--
-- Аддитивно и идемпотентно. Откат:
--   ALTER TABLE communities DROP COLUMN IF EXISTS disabled_at;
--   ALTER TABLE communities DROP COLUMN IF EXISTS disabled_reason;

ALTER TABLE communities ADD COLUMN IF NOT EXISTS disabled_at TIMESTAMP NULL;
ALTER TABLE communities ADD COLUMN IF NOT EXISTS disabled_reason VARCHAR(50) NULL;

-- Backfill: 59 dead, вынесенные миграцией 050 (2026-06-30), получают явную
-- причину — чтобы digest dormant-политики их не путал со своими.
UPDATE communities
SET disabled_reason = 'dead_migration_050'
WHERE is_active = false
  AND health_status = 'dead'
  AND disabled_reason IS NULL;
