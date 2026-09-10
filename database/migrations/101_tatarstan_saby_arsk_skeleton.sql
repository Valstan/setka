-- 101: skeleton for the first two NEW raions of Tatarstan: saby (Сабинский), arsk (Арский).
-- Same pattern as the Kirov batches 061-071: INACTIVE regions with vk_group_id NULL —
-- the "- ИНФО" groups are created after this migration (owner's decision 2026-09-10:
-- the agent creates them in the owner's browser). Additive and reversible.
--
-- Why these two: Сабинский is the only district bordering BOTH existing Tatarstan raions
-- (bal, kukmor); Арский is the largest neighbour of Балтаси (51k, 60 km from Kazan).
-- Together they form one contiguous patch Балтаси–Кукмор–Сабы–Арск, so the neighbour
-- exchange works inside the patch from day one. Borders checked against ru.wikipedia
-- (Кукморский / Балтасинский / Сабинский / Арский районы), not written from memory.
--
-- Branding: full 9-theme Kirov template, «района» (Tatarstan has municipal districts,
-- not okrugs). bal/kukmor still carry the 5-theme Mongo legacy — not touched here.
-- raicentr goes into hashtags (#<raicentr>, «спорт<raicentr>»), so no spaces:
-- «Богатые Сабы» → «Сабы».
-- Localities: the ten largest settlement centres per ru.wikipedia; «посёлок Лесхоз»
-- (Мешинское СП) left out on purpose — too generic a name, it would match everywhere.
--
-- Neighbours: symmetric. bal/kukmor get the new codes APPENDED to their current values
-- (read from prod 2026-09-10). Sources of neighbours resolve with
-- `is_active = TRUE AND vk_group_id IS NOT NULL`, so for the active bal/kukmor this is a
-- no-op until saby/arsk are activated.

BEGIN;

INSERT INTO regions (code, name, vk_group_id, kind, parent_region_id, is_active)
SELECT v.code, v.name, NULL, 'raion', (SELECT id FROM regions WHERE code = 'tatarstan_obl'), FALSE
FROM (VALUES
    ('saby', 'САБЫ - ИНФО'),
    ('arsk', 'АРСК - ИНФО')
) AS v(code, name)
WHERE NOT EXISTS (SELECT 1 FROM regions r WHERE r.code = v.code);

INSERT INTO region_configs (region_code, zagolovki, heshteg_local, text_post_maxsize_simbols, localities, created_at, updated_at)
SELECT * FROM (VALUES
(
    'saby',
    '{
        "novost": "Новости Сабинского района:",
        "reklama": "Объявления Сабинского района:",
        "kultura": "Культура Сабинского района:",
        "sport": "Спорт Сабинского района:",
        "admin": "Власть и общество Сабинского района:",
        "union": "Молодёжь и образование Сабинского района:",
        "detsad": "Детские сады Сабинского района:",
        "sosed": "Происшествия Сабинского района:",
        "addons": "Сабинский район — также:"
    }'::json,
    '{"raicentr": "Сабы"}'::json,
    4096,
    '["Богатые Сабы", "Шемордан", "Измя", "Тимершик", "Сатышево", "Большой Шинар", "Большие Кибячи", "Кильдебяк", "Шикши", "Корсабаш"]'::json,
    (now() AT TIME ZONE 'utc'), (now() AT TIME ZONE 'utc')
),
(
    'arsk',
    '{
        "novost": "Новости Арского района:",
        "reklama": "Объявления Арского района:",
        "kultura": "Культура Арского района:",
        "sport": "Спорт Арского района:",
        "admin": "Власть и общество Арского района:",
        "union": "Молодёжь и образование Арска:",
        "detsad": "Детские сады Арска:",
        "sosed": "Происшествия Арского района:",
        "addons": "Арский район — также:"
    }'::json,
    '{"raicentr": "Арск"}'::json,
    4096,
    '["Арск", "Новый Кинер", "Урняк", "Смак-Корса", "Ашитбаш", "Шушмабаш", "Нижние Метески", "Апазово", "Новый Кырлай", "Старое Чурилино"]'::json,
    (now() AT TIME ZONE 'utc'), (now() AT TIME ZONE 'utc')
)
) AS v(region_code, zagolovki, heshteg_local, text_post_maxsize_simbols, localities, created_at, updated_at)
WHERE NOT EXISTS (SELECT 1 FROM region_configs rc WHERE rc.region_code = v.region_code);

UPDATE regions SET neighbors = 'arsk,bal,kukmor' WHERE code = 'saby';
UPDATE regions SET neighbors = 'bal,saby' WHERE code = 'arsk';
UPDATE regions SET neighbors = 'arsk,klz,kukmor,mi,saby,ur,vp' WHERE code = 'bal';
UPDATE regions SET neighbors = 'bal,klz,mi,saby,ur,vp' WHERE code = 'kukmor';

COMMIT;

-- Проверка после применения:
--   SELECT code, name, is_active, vk_group_id, neighbors FROM regions
--   WHERE code IN ('saby','arsk','bal','kukmor') ORDER BY code;
-- Симметричность (пустая выдача = все связи двусторонние):
--   SELECT a.code, b.code FROM regions a JOIN regions b
--     ON position(b.code in a.neighbors) > 0
--   WHERE position(a.code in coalesce(b.neighbors,'')) = 0;
--
-- Rollback:
-- UPDATE regions SET neighbors = 'klz,kukmor,mi,ur,vp' WHERE code = 'bal';
-- UPDATE regions SET neighbors = 'bal,klz,mi,ur,vp' WHERE code = 'kukmor';
-- DELETE FROM region_configs WHERE region_code IN ('saby','arsk');
-- DELETE FROM regions WHERE code IN ('saby','arsk');
