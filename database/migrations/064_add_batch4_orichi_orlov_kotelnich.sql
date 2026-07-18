-- 064: skeleton for batch-4 new raions of Kirov oblast: orichi, orlov, kotelnich.
-- Same pattern as 061-063: INACTIVE regions with vk_group_id NULL — main "- ИНФО"
-- groups created by the owner after 2026-07-20. Additive and reversible.
-- kotelnich covers both the town (separate urban okrug administratively) and the raion.

BEGIN;

INSERT INTO regions (code, name, vk_group_id, kind, parent_region_id, is_active)
SELECT v.code, v.name, NULL, 'raion', (SELECT id FROM regions WHERE code = 'kirov_obl'), FALSE
FROM (VALUES
    ('orichi',    'ОРИЧИ - ИНФО'),
    ('orlov',     'ОРЛОВ - ИНФО'),
    ('kotelnich', 'КОТЕЛЬНИЧ - ИНФО')
) AS v(code, name)
WHERE NOT EXISTS (SELECT 1 FROM regions r WHERE r.code = v.code);

INSERT INTO region_configs (region_code, zagolovki, heshteg_local, text_post_maxsize_simbols, localities, created_at, updated_at)
SELECT * FROM (VALUES
(
    'orichi',
    '{
        "novost": "Новости Оричевского района:",
        "reklama": "Объявления Оричевского района:",
        "kultura": "Культура Оричевского района:",
        "sport": "Спорт Оричевского района:",
        "admin": "Власть и общество Оричевского района:",
        "union": "Молодёжь и образование Оричей:",
        "detsad": "Детские сады Оричей:",
        "sosed": "Происшествия Оричевского района:",
        "addons": "Оричевский район — также:"
    }'::json,
    '{"raicentr": "Оричи"}'::json,
    4096,
    '["Оричи", "Истобенск", "Коршик", "Адышево", "Мирный", "Стрижи", "Лёвинцы", "Торфяной", "Юбилейный", "Зенгино", "Быстрица", "Пищалье", "Шалегово", "Спас-Талица", "Суводи", "Пустоши", "Монастырщина", "Большие Гари", "Быстряги", "Шевнины"]'::json,
    now(), now()
),
(
    'orlov',
    '{
        "novost": "Новости Орловского района:",
        "reklama": "Объявления Орловского района:",
        "kultura": "Культура Орловского района:",
        "sport": "Спорт Орловского района:",
        "admin": "Власть и общество Орловского района:",
        "union": "Молодёжь и образование Орлова:",
        "detsad": "Детские сады Орлова:",
        "sosed": "Происшествия Орловского района:",
        "addons": "Орловский район — также:"
    }'::json,
    '{"raicentr": "Орлов"}'::json,
    4096,
    '["Орлов", "Цепели", "Тохтино", "Чудиново", "Колково", "Русаново", "Степановщина", "Соловецкое", "Кленовица", "Моржи", "Лугиновка", "Шадричи", "Красногоры", "Селичи", "Куликовщина"]'::json,
    now(), now()
),
(
    'kotelnich',
    '{
        "novost": "Новости Котельнича и района:",
        "reklama": "Объявления Котельнича и района:",
        "kultura": "Культура Котельнича и района:",
        "sport": "Спорт Котельнича и района:",
        "admin": "Власть и общество Котельнича и района:",
        "union": "Молодёжь и образование Котельнича:",
        "detsad": "Детские сады Котельнича:",
        "sosed": "Происшествия Котельнича и района:",
        "addons": "Котельнич и район — также:"
    }'::json,
    '{"raicentr": "Котельнич"}'::json,
    4096,
    '["Котельнич", "Ленинская Искра", "Светлый", "Макарье", "Карпушино", "Комсомольский", "Юбилейный", "Боровка", "Вишкиль", "Александровское", "Покровское", "Спасское", "Сретенье", "Красногорье", "Молотниково", "Ежиха", "Зайцевы", "Гулины", "Родичи"]'::json,
    now(), now()
)
) AS v(region_code, zagolovki, heshteg_local, text_post_maxsize_simbols, localities, created_at, updated_at)
WHERE NOT EXISTS (SELECT 1 FROM region_configs rc WHERE rc.region_code = v.region_code);

COMMIT;

-- Rollback:
-- DELETE FROM region_configs WHERE region_code IN ('orichi','orlov','kotelnich');
-- DELETE FROM regions WHERE code IN ('orichi','orlov','kotelnich');
