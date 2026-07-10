-- 058: persist VK error_code последнего health-recheck'а сообщества
-- (бэклог discovery, brain 2026-06-30: «разблокирует honest dead/unreachable split»).
--
-- Раньше recheck различал коды только в моменте (CommunityHealth.error_code
-- не сохранялся) — dead-ведро было неразделимо на «удалён навсегда» (18/100,
-- можно kill) и «недоступен — приватность/бан токена/РКН» (15/203, нужен
-- re-probe перед kill, cf #041). NULL = последний recheck прошёл без ошибки.
--
-- Применение: ssh setka 'sudo -u postgres psql -d setka -f /home/valstan/SETKA/database/migrations/058_community_last_error_code.sql'
-- Откат:      ALTER TABLE communities DROP COLUMN last_error_code;

ALTER TABLE communities ADD COLUMN IF NOT EXISTS last_error_code INTEGER NULL;

COMMENT ON COLUMN communities.last_error_code IS
    'VK error_code последнего health-recheck (NULL = без ошибки); 18/100 = удалён, 15/203 = недоступен (re-probe перед kill)';
