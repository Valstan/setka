-- 072: branding for Radar-ID login page (единый вход экосистемы).
-- oauth_clients.branding JSON: {"title": "...", "icon": "...", "accent": "#hex", "sub": "..."}
-- NULL = fallback на oauth_clients.name. Additive and reversible.

BEGIN;

ALTER TABLE oauth_clients ADD COLUMN IF NOT EXISTS branding JSON;

COMMIT;

-- Rollback:
-- ALTER TABLE oauth_clients DROP COLUMN IF EXISTS branding;
