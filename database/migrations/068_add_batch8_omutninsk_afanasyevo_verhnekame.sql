-- 068: skeleton for batch-8 new raions of Kirov oblast: omutninsk, afanasyevo, verhnekame.
-- Same pattern as 061-067: INACTIVE regions with vk_group_id NULL — main "- ИНФО"
-- groups created by the owner after 2026-07-20. Additive and reversible.
-- verhnekame raion center is the town of Kirs.

BEGIN;

INSERT INTO regions (code, name, vk_group_id, kind, parent_region_id, is_active)
SELECT v.code, v.name, NULL, 'raion', (SELECT id FROM regions WHERE code = 'kirov_obl'), FALSE
FROM (VALUES
    ('omutninsk',  'ОМУТНИНСК - ИНФО'),
    ('afanasyevo', 'АФАНАСЬЕВО - ИНФО'),
    ('verhnekame', 'КИРС - ИНФО')
) AS v(code, name)
WHERE NOT EXISTS (SELECT 1 FROM regions r WHERE r.code = v.code);

INSERT INTO region_configs (region_code, zagolovki, heshteg_local, text_post_maxsize_simbols, localities, created_at, updated_at)
SELECT * FROM (VALUES
(
    'omutninsk',
    '{
        "novost": "Новости Омутнинского района:",
        "reklama": "Объявления Омутнинского района:",
        "kultura": "Культура Омутнинского района:",
        "sport": "Спорт Омутнинского района:",
        "admin": "Власть и общество Омутнинского района:",
        "union": "Молодёжь и образование Омутнинска:",
        "detsad": "Детские сады Омутнинска:",
        "sosed": "Происшествия Омутнинского района:",
        "addons": "Омутнинский район — также:"
    }'::json,
    '{"raicentr": "Омутнинск"}'::json,
    4096,
    '["Омутнинск", "Восточный", "Песковка", "Котчиха", "Чёрная Холуница", "Белореченск", "Залазна", "Лесные Поляны", "Струговский", "Шахровка"]'::json,
    now(), now()
),
(
    'afanasyevo',
    '{
        "novost": "Новости Афанасьевского округа:",
        "reklama": "Объявления Афанасьевского округа:",
        "kultura": "Культура Афанасьевского округа:",
        "sport": "Спорт Афанасьевского округа:",
        "admin": "Власть и общество Афанасьевского округа:",
        "union": "Молодёжь и образование Афанасьево:",
        "detsad": "Детские сады Афанасьево:",
        "sosed": "Происшествия Афанасьевского округа:",
        "addons": "Афанасьевский округ — также:"
    }'::json,
    '{"raicentr": "Афанасьево"}'::json,
    4096,
    '["Афанасьево", "Бисерово", "Гордино", "Пашино", "Лытка", "Ичетовкины", "Камский", "Кувакуш", "Георгиево", "Бор", "Томызь", "Савинцы"]'::json,
    now(), now()
),
(
    'verhnekame',
    '{
        "novost": "Новости Верхнекамского округа:",
        "reklama": "Объявления Верхнекамского округа:",
        "kultura": "Культура Верхнекамского округа:",
        "sport": "Спорт Верхнекамского округа:",
        "admin": "Власть и общество Верхнекамского округа:",
        "union": "Молодёжь и образование Кирса:",
        "detsad": "Детские сады Кирса:",
        "sosed": "Происшествия Верхнекамского округа:",
        "addons": "Верхнекамский округ — также:"
    }'::json,
    '{"raicentr": "Кирс"}'::json,
    4096,
    '["Кирс", "Лесной", "Рудничный", "Светлополянск", "Лойно", "Созимский", "Кай", "Чус", "Ожмегово", "Сорда", "Кочкино", "Барановка"]'::json,
    now(), now()
)
) AS v(region_code, zagolovki, heshteg_local, text_post_maxsize_simbols, localities, created_at, updated_at)
WHERE NOT EXISTS (SELECT 1 FROM region_configs rc WHERE rc.region_code = v.region_code);

COMMIT;

-- Rollback:
-- DELETE FROM region_configs WHERE region_code IN ('omutninsk','afanasyevo','verhnekame');
-- DELETE FROM regions WHERE code IN ('omutninsk','afanasyevo','verhnekame');
