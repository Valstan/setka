-- 071: skeleton for batch-9 (final) new raions of Kirov oblast: luza, podosinovets, oparino.
-- Same pattern as 061-068: INACTIVE regions with vk_group_id NULL — main "- ИНФО"
-- groups created by the owner after 2026-07-20. Additive and reversible.
-- Closes the north-west corner: with this batch the whole oblast is skeleton-ready.

BEGIN;

INSERT INTO regions (code, name, vk_group_id, kind, parent_region_id, is_active)
SELECT v.code, v.name, NULL, 'raion', (SELECT id FROM regions WHERE code = 'kirov_obl'), FALSE
FROM (VALUES
    ('luza',         'ЛУЗА - ИНФО'),
    ('podosinovets', 'ПОДОСИНОВЕЦ - ИНФО'),
    ('oparino',      'ОПАРИНО - ИНФО')
) AS v(code, name)
WHERE NOT EXISTS (SELECT 1 FROM regions r WHERE r.code = v.code);

INSERT INTO region_configs (region_code, zagolovki, heshteg_local, text_post_maxsize_simbols, localities, created_at, updated_at)
SELECT * FROM (VALUES
(
    'luza',
    '{
        "novost": "Новости Лузского округа:",
        "reklama": "Объявления Лузского округа:",
        "kultura": "Культура Лузского округа:",
        "sport": "Спорт Лузского округа:",
        "admin": "Власть и общество Лузского округа:",
        "union": "Молодёжь и образование Лузы:",
        "detsad": "Детские сады Лузы:",
        "sosed": "Происшествия Лузского округа:",
        "addons": "Лузский округ — также:"
    }'::json,
    '{"raicentr": "Луза"}'::json,
    4096,
    '["Луза", "Лальск", "Христофорово", "Папулово", "Верхнелалье", "Учка", "Грибошино", "Заречье", "Таврический", "Чекавино"]'::json,
    now(), now()
),
(
    'podosinovets',
    '{
        "novost": "Новости Подосиновского округа:",
        "reklama": "Объявления Подосиновского округа:",
        "kultura": "Культура Подосиновского округа:",
        "sport": "Спорт Подосиновского округа:",
        "admin": "Власть и общество Подосиновского округа:",
        "union": "Молодёжь и образование Подосиновца:",
        "detsad": "Детские сады Подосиновца:",
        "sosed": "Происшествия Подосиновского округа:",
        "addons": "Подосиновский округ — также:"
    }'::json,
    '{"raicentr": "Подосиновец"}'::json,
    4096,
    '["Подосиновец", "Демьяново", "Пинюг", "Яхреньга", "Утманово", "Октябрь", "Лодейно", "Щёткино", "Лунданка", "Скрябино"]'::json,
    now(), now()
),
(
    'oparino',
    '{
        "novost": "Новости Опаринского округа:",
        "reklama": "Объявления Опаринского округа:",
        "kultura": "Культура Опаринского округа:",
        "sport": "Спорт Опаринского округа:",
        "admin": "Власть и общество Опаринского округа:",
        "union": "Молодёжь и образование Опарино:",
        "detsad": "Детские сады Опарино:",
        "sosed": "Происшествия Опаринского округа:",
        "addons": "Опаринский округ — также:"
    }'::json,
    '{"raicentr": "Опарино"}'::json,
    4096,
    '["Опарино", "Маромица", "Заря", "Стрельская", "Вазюк", "Альмеж", "Шабуры", "Речной", "Молома", "Латышский"]'::json,
    now(), now()
)
) AS v(region_code, zagolovki, heshteg_local, text_post_maxsize_simbols, localities, created_at, updated_at)
WHERE NOT EXISTS (SELECT 1 FROM region_configs rc WHERE rc.region_code = v.region_code);

COMMIT;

-- Rollback:
-- DELETE FROM region_configs WHERE region_code IN ('luza','podosinovets','oparino');
-- DELETE FROM regions WHERE code IN ('luza','podosinovets','oparino');
