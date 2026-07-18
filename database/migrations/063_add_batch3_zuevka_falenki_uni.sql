-- 063: skeleton for batch-3 new raions of Kirov oblast: zuevka, falenki, uni.
-- Same pattern as 061/062: INACTIVE regions with vk_group_id NULL — main "- ИНФО"
-- groups created by the owner after 2026-07-20; activation is a separate step.
-- Additive and reversible (rollback at the bottom).

BEGIN;

INSERT INTO regions (code, name, vk_group_id, kind, parent_region_id, is_active)
SELECT v.code, v.name, NULL, 'raion', (SELECT id FROM regions WHERE code = 'kirov_obl'), FALSE
FROM (VALUES
    ('zuevka',  'ЗУЕВКА - ИНФО'),
    ('falenki', 'ФАЛЁНКИ - ИНФО'),
    ('uni',     'УНИ - ИНФО')
) AS v(code, name)
WHERE NOT EXISTS (SELECT 1 FROM regions r WHERE r.code = v.code);

INSERT INTO region_configs (region_code, zagolovki, heshteg_local, text_post_maxsize_simbols, localities, created_at, updated_at)
SELECT * FROM (VALUES
(
    'zuevka',
    '{
        "novost": "Новости Зуевского района:",
        "reklama": "Объявления Зуевского района:",
        "kultura": "Культура Зуевского района:",
        "sport": "Спорт Зуевского района:",
        "admin": "Власть и общество Зуевского района:",
        "union": "Молодёжь и образование Зуевки:",
        "detsad": "Детские сады Зуевки:",
        "sosed": "Происшествия Зуевского района:",
        "addons": "Зуевский район — также:"
    }'::json,
    '{"raicentr": "Зуевка"}'::json,
    4096,
    '["Зуевка", "Косино", "Мухино", "Соколовка", "Сезенево", "Семушино", "Рябово", "Лема", "Кордяга", "Спасо-Заозерье", "Ардаши", "Зуи", "Октябрьский", "Березовка", "Кокоренцы", "Салтыки", "Целоусы", "Мусихи", "Левинцы", "Слудка", "Хмелевка"]'::json,
    now(), now()
),
(
    'falenki',
    '{
        "novost": "Новости Фалёнского округа:",
        "reklama": "Объявления Фалёнского округа:",
        "kultura": "Культура Фалёнского округа:",
        "sport": "Спорт Фалёнского округа:",
        "admin": "Власть и общество Фалёнского округа:",
        "union": "Молодёжь и образование Фалёнок:",
        "detsad": "Детские сады Фалёнок:",
        "sosed": "Происшествия Фалёнского округа:",
        "addons": "Фалёнский округ — также:"
    }'::json,
    '{"raicentr": "Фалёнки"}'::json,
    4096,
    '["Фалёнки", "Святица", "Низево", "Леваны", "Подоплеки", "Белая", "Николаево", "Талица", "Полом", "Медвежена", "Филейка", "Баженово", "Вогульцы", "Ильинское", "Паньшонки", "Петруненки", "Русская Сада", "Солдари", "Юсово", "Чепецкий"]'::json,
    now(), now()
),
(
    'uni',
    '{
        "novost": "Новости Унинского округа:",
        "reklama": "Объявления Унинского округа:",
        "kultura": "Культура Унинского округа:",
        "sport": "Спорт Унинского округа:",
        "admin": "Власть и общество Унинского округа:",
        "union": "Молодёжь и образование Уней:",
        "detsad": "Детские сады Уней:",
        "sosed": "Происшествия Унинского округа:",
        "addons": "Унинский округ — также:"
    }'::json,
    '{"raicentr": "Уни"}'::json,
    4096,
    '["Уни", "Порез", "Елгань", "Сардык", "Малый Полом", "Уть", "Канахинцы", "Русские Тимши", "Удмуртские Тимши", "Удмуртский Порез", "Удмуртский Сурвай", "Большая Дуброва", "Комарово", "Малиновка", "Малые Уни", "Верхолемье", "Лумпун", "Барашки", "Чуваши", "Урай"]'::json,
    now(), now()
)
) AS v(region_code, zagolovki, heshteg_local, text_post_maxsize_simbols, localities, created_at, updated_at)
WHERE NOT EXISTS (SELECT 1 FROM region_configs rc WHERE rc.region_code = v.region_code);

COMMIT;

-- Rollback:
-- DELETE FROM region_configs WHERE region_code IN ('zuevka','falenki','uni');
-- DELETE FROM regions WHERE code IN ('zuevka','falenki','uni');
