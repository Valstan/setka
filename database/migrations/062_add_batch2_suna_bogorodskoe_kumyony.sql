-- 062: skeleton for batch-2 new raions of Kirov oblast: suna, bogorodskoe, kumyony.
-- Same pattern as 061: INACTIVE regions with vk_group_id NULL — the main "- ИНФО"
-- VK groups will be created by the owner after 2026-07-20; activation is a separate
-- manual step. Additive and reversible (rollback at the bottom).

BEGIN;

INSERT INTO regions (code, name, vk_group_id, kind, parent_region_id, is_active)
SELECT v.code, v.name, NULL, 'raion', (SELECT id FROM regions WHERE code = 'kirov_obl'), FALSE
FROM (VALUES
    ('suna',        'СУНА - ИНФО'),
    ('bogorodskoe', 'БОГОРОДСКОЕ - ИНФО'),
    ('kumyony',     'КУМЁНЫ - ИНФО')
) AS v(code, name)
WHERE NOT EXISTS (SELECT 1 FROM regions r WHERE r.code = v.code);

INSERT INTO region_configs (region_code, zagolovki, heshteg_local, text_post_maxsize_simbols, localities, created_at, updated_at)
SELECT * FROM (VALUES
(
    'suna',
    '{
        "novost": "Новости Сунского округа:",
        "reklama": "Объявления Сунского округа:",
        "kultura": "Культура Сунского округа:",
        "sport": "Спорт Сунского округа:",
        "admin": "Власть и общество Сунского округа:",
        "union": "Молодёжь и образование Суны:",
        "detsad": "Детские сады Суны:",
        "sosed": "Происшествия Сунского округа:",
        "addons": "Сунский округ — также:"
    }'::json,
    '{"raicentr": "Суна"}'::json,
    4096,
    '["Суна", "Верхосунье", "Нестино", "Курчум", "Ошеть", "Краснополье", "Большевик", "Мурино", "Плелое", "Кокуй", "Большие Туры", "Малые Туры", "Тоскуй", "Гребёнки", "Смыки", "Дворища", "Здерихино", "Горбуново", "Опан", "Перескоки", "Лебедка", "Киселиха", "Окуневская", "Кузнецы", "Бородули", "Шатки", "Булдаки", "Верхорубы", "Камешница", "Осиновица", "Савиново", "Темерево", "Копырята", "Каширцы", "Боталы", "Шиврино", "Бабино", "Кушкалово", "Софроны"]'::json,
    now(), now()
),
(
    'bogorodskoe',
    '{
        "novost": "Новости Богородского округа:",
        "reklama": "Объявления Богородского округа:",
        "kultura": "Культура Богородского округа:",
        "sport": "Спорт Богородского округа:",
        "admin": "Власть и общество Богородского округа:",
        "union": "Молодёжь и образование Богородского:",
        "detsad": "Детские сады Богородского:",
        "sosed": "Происшествия Богородского округа:",
        "addons": "Богородский округ — также:"
    }'::json,
    '{"raicentr": "Богородское"}'::json,
    4096,
    '["Богородское", "Ошлань", "Ухтым", "Хороши", "Таранки", "Лобань", "Спасское", "Верховойское", "Митроки", "Мухачи", "Бошары", "Ворсик", "Рождественское", "Рябины", "Сарапулы", "Туманы", "Ходыри", "Чирки"]'::json,
    now(), now()
),
(
    'kumyony',
    '{
        "novost": "Новости Кумёнского района:",
        "reklama": "Объявления Кумёнского района:",
        "kultura": "Культура Кумёнского района:",
        "sport": "Спорт Кумёнского района:",
        "admin": "Власть и общество Кумёнского района:",
        "union": "Молодёжь и образование Кумён:",
        "detsad": "Детские сады Кумён:",
        "sosed": "Происшествия Кумёнского района:",
        "addons": "Кумёнский район — также:"
    }'::json,
    '{"raicentr": "Кумёны"}'::json,
    4096,
    '["Кумёны", "Вожгалы", "Вичевщина", "Нижнеивкино", "Верхобыстрица", "Кырмыж", "Речной", "Краснооктябрьский", "Большой Перелаз", "Березник", "Лутошкино", "Моряны", "Вересники", "Быково", "Раменье", "Красногорье", "Закаринье", "Дымково", "Карино", "Полом", "Суслопары", "Рябиново", "Медведи", "Мокино", "Слудное", "Спасская", "Юнка", "Юньга"]'::json,
    now(), now()
)
) AS v(region_code, zagolovki, heshteg_local, text_post_maxsize_simbols, localities, created_at, updated_at)
WHERE NOT EXISTS (SELECT 1 FROM region_configs rc WHERE rc.region_code = v.region_code);

COMMIT;

-- Rollback:
-- DELETE FROM region_configs WHERE region_code IN ('suna','bogorodskoe','kumyony');
-- DELETE FROM regions WHERE code IN ('suna','bogorodskoe','kumyony');
