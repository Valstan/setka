-- 065: skeleton for batch-5 new raions of Kirov oblast: slobodskoy, belholunitsa, chepetsk.
-- Same pattern as 061-064: INACTIVE regions with vk_group_id NULL — main "- ИНФО"
-- groups created by the owner after 2026-07-20. Additive and reversible.
-- slobodskoy and chepetsk cover both the town (separate urban okrug) and the raion.

BEGIN;

INSERT INTO regions (code, name, vk_group_id, kind, parent_region_id, is_active)
SELECT v.code, v.name, NULL, 'raion', (SELECT id FROM regions WHERE code = 'kirov_obl'), FALSE
FROM (VALUES
    ('slobodskoy',   'СЛОБОДСКОЙ - ИНФО'),
    ('belholunitsa', 'БЕЛАЯ ХОЛУНИЦА - ИНФО'),
    ('chepetsk',     'КИРОВО-ЧЕПЕЦК - ИНФО')
) AS v(code, name)
WHERE NOT EXISTS (SELECT 1 FROM regions r WHERE r.code = v.code);

INSERT INTO region_configs (region_code, zagolovki, heshteg_local, text_post_maxsize_simbols, localities, created_at, updated_at)
SELECT * FROM (VALUES
(
    'slobodskoy',
    '{
        "novost": "Новости Слободского и района:",
        "reklama": "Объявления Слободского и района:",
        "kultura": "Культура Слободского и района:",
        "sport": "Спорт Слободского и района:",
        "admin": "Власть и общество Слободского и района:",
        "union": "Молодёжь и образование Слободского:",
        "detsad": "Детские сады Слободского:",
        "sosed": "Происшествия Слободского и района:",
        "addons": "Слободской и район — также:"
    }'::json,
    '{"raicentr": "Слободской"}'::json,
    4096,
    '["Слободской", "Вахруши", "Бобино", "Ильинское", "Карино", "Закаринье", "Светозарево", "Совье", "Лекма", "Шестаково", "Стулово", "Шихово", "Боровица", "Озерница", "Холуново", "Денисовы", "Зониха", "Центральный", "Сухоборка", "Октябрьский"]'::json,
    now(), now()
),
(
    'belholunitsa',
    '{
        "novost": "Новости Белохолуницкого района:",
        "reklama": "Объявления Белохолуницкого района:",
        "kultura": "Культура Белохолуницкого района:",
        "sport": "Спорт Белохолуницкого района:",
        "admin": "Власть и общество Белохолуницкого района:",
        "union": "Молодёжь и образование Белой Холуницы:",
        "detsad": "Детские сады Белой Холуницы:",
        "sosed": "Происшествия Белохолуницкого района:",
        "addons": "Белохолуницкий район — также:"
    }'::json,
    '{"raicentr": "Белая Холуница"}'::json,
    4096,
    '["Белая Холуница", "Климковка", "Дубровка", "Подрезчиха", "Прокопье", "Троица", "Всехсвятское", "Быданово", "Ракалово", "Сырьяны", "Пантыл", "Гуренки", "Иванцево", "Великое Поле", "Каменное"]'::json,
    now(), now()
),
(
    'chepetsk',
    '{
        "novost": "Новости Кирово-Чепецка и района:",
        "reklama": "Объявления Кирово-Чепецка и района:",
        "kultura": "Культура Кирово-Чепецка и района:",
        "sport": "Спорт Кирово-Чепецка и района:",
        "admin": "Власть и общество Кирово-Чепецка и района:",
        "union": "Молодёжь и образование Кирово-Чепецка:",
        "detsad": "Детские сады Кирово-Чепецка:",
        "sosed": "Происшествия Кирово-Чепецка и района:",
        "addons": "Кирово-Чепецк и район — также:"
    }'::json,
    '{"raicentr": "Кирово-Чепецк"}'::json,
    4096,
    '["Кирово-Чепецк", "Просница", "Пасегово", "Кстинино", "Филиппово", "Фатеево", "Каринка", "Селезениха", "Бурмакино", "Малый Конып", "Лубягино", "Шутовщина", "Пригородный", "Перекоп", "Дресвяново", "Марковцы", "Поломец"]'::json,
    now(), now()
)
) AS v(region_code, zagolovki, heshteg_local, text_post_maxsize_simbols, localities, created_at, updated_at)
WHERE NOT EXISTS (SELECT 1 FROM region_configs rc WHERE rc.region_code = v.region_code);

COMMIT;

-- Rollback:
-- DELETE FROM region_configs WHERE region_code IN ('slobodskoy','belholunitsa','chepetsk');
-- DELETE FROM regions WHERE code IN ('slobodskoy','belholunitsa','chepetsk');
