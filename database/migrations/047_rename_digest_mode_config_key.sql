-- 047_rename_digest_mode_config_key.sql
--
-- Хвост переименования digest → bulletin (PR-5): JSON-ключ `digest_mode` в колонке
-- regions.config → `bulletin_mode`. Ключ управляет community-mode маршрутизацией
-- (kirov_obl / tatarstan_obl ведут себя как район), читается в
-- tasks/parsing_scheduler_tasks.py через Region.config ->> 'bulletin_mode'.
--
-- Код уже читает 'bulletin_mode' (PR-5); эта миграция переносит данные, чтобы 2
-- области не выпали из маршрутизации. Идемпотентно (по наличию ключа). config —
-- тип json, кастуем к jsonb и обратно.

UPDATE regions
SET config = (
        (config::jsonb - 'digest_mode')
        || jsonb_build_object('bulletin_mode', config::jsonb->'digest_mode')
    )::json
WHERE config::jsonb ? 'digest_mode';
