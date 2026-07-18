-- 061: skeleton for batch-1 new raions of Kirov oblast: yaransk, sanchursk, kiknur.
-- Regions are created INACTIVE with vk_group_id NULL: the main "- ИНФО" VK groups
-- will be created by the owner's account after 2026-07-20 (unblock); activation is a
-- separate manual step (UPDATE vk_group_id + is_active=true) once groups exist.
-- Additive and reversible: DELETE FROM region_configs / regions WHERE code IN (...).

BEGIN;

INSERT INTO regions (code, name, vk_group_id, kind, parent_region_id, is_active)
SELECT v.code, v.name, NULL, 'raion', (SELECT id FROM regions WHERE code = 'kirov_obl'), FALSE
FROM (VALUES
    ('yaransk',   'ЯРАНСК - ИНФО'),
    ('sanchursk', 'САНЧУРСК - ИНФО'),
    ('kiknur',    'КИКНУР - ИНФО')
) AS v(code, name)
WHERE NOT EXISTS (SELECT 1 FROM regions r WHERE r.code = v.code);

INSERT INTO region_configs (region_code, zagolovki, heshteg_local, text_post_maxsize_simbols, localities, created_at, updated_at)
SELECT * FROM (VALUES
(
    'yaransk',
    '{
        "novost": "Новости Яранского района:",
        "reklama": "Объявления Яранского района:",
        "kultura": "Культура Яранского района:",
        "sport": "Спорт Яранского района:",
        "admin": "Власть и общество Яранского района:",
        "union": "Молодёжь и образование Яранска:",
        "detsad": "Детские сады Яранска:",
        "sosed": "Происшествия Яранского района:",
        "addons": "Яранский район — также:"
    }'::json,
    '{"raicentr": "Яранск"}'::json,
    4096,
    '["Яранск", "Салобеляк", "Знаменка", "Кугалки", "Никола", "Опытное Поле", "Первомайское", "Пиштань", "Рождественское", "Сердеж", "Уртма", "Шкаланка", "Мари-Ушем", "Большая Кугушерга", "Кугушерга", "Малая Кугушерга", "Каракша", "Большая Каракша", "Савичи", "Ерши", "Лум", "Люя", "Энгенер", "Побекнур", "Урлум", "Катанур", "Вилюнур", "Большие Шалаи", "Малые Шалаи", "Большая Лайка", "Мари-Дубники", "Пержа", "Шошма", "Юльял", "Черканер", "Шуймар-Верховская", "Шуймар-Заречная", "Люметь-Поле", "Мосуны", "Никулята", "Кукмар", "Кукодор", "Козловаж", "Верхоижье", "Верхоуслино", "Танаково"]'::json,
    now(), now()
),
(
    'sanchursk',
    '{
        "novost": "Новости Санчурского округа:",
        "reklama": "Объявления Санчурского округа:",
        "kultura": "Культура Санчурского округа:",
        "sport": "Спорт Санчурского округа:",
        "admin": "Власть и общество Санчурского округа:",
        "union": "Молодёжь и образование Санчурска:",
        "detsad": "Детские сады Санчурска:",
        "sosed": "Происшествия Санчурского округа:",
        "addons": "Санчурский округ — также:"
    }'::json,
    '{"raicentr": "Санчурск"}'::json,
    4096,
    '["Санчурск", "Матвинур", "Корляки", "Кувшинское", "Люмпанур", "Мусерье", "Сметанино", "Великоречье", "Вотчина", "Галицкое", "Икманур", "Ошманур", "Лопанур", "Марийская Лиса", "Большая Русская Лиса", "Малая Русская Лиса", "Марийское Кубашево", "Русское Кубашево", "Тарханы", "Курдюм", "Кундыш-Мучакш", "Соболево", "Сухоречье", "Большая Шишовка", "Малая Шишовка", "Большое Киримбаево", "Большой Ихтиал", "Большой Краснояр", "Витьюм", "Легканур", "Изинур", "Ихта", "Дмитриевская Патья", "Марьинская Патья", "Вотчинский Кунер"]'::json,
    now(), now()
),
(
    'kiknur',
    '{
        "novost": "Новости Кикнурского округа:",
        "reklama": "Объявления Кикнурского округа:",
        "kultura": "Культура Кикнурского округа:",
        "sport": "Спорт Кикнурского округа:",
        "admin": "Власть и общество Кикнурского округа:",
        "union": "Молодёжь и образование Кикнура:",
        "detsad": "Детские сады Кикнура:",
        "sosed": "Происшествия Кикнурского округа:",
        "addons": "Кикнурский округ — также:"
    }'::json,
    '{"raicentr": "Кикнур"}'::json,
    4096,
    '["Кикнур", "Русские Краи", "Кокшага", "Шапта", "Цекеево", "Потняк", "Ваштранга", "Макарье", "Беляево", "Улеш", "Тырышкино", "Падерино", "Большое Шарыгино", "Малое Шарыгино", "Пайбулатово", "Панчурга", "Кушнур", "Майда", "Пама", "Большая Люя", "Большой Кулянур", "Большой Шудум", "Русская Толшева", "Марийская Толшева", "Пижанчурга", "Пелеснур", "Ендур", "Каргазы", "Гуслянка", "Юльял", "Кукнур"]'::json,
    now(), now()
)
) AS v(region_code, zagolovki, heshteg_local, text_post_maxsize_simbols, localities, created_at, updated_at)
WHERE NOT EXISTS (SELECT 1 FROM region_configs rc WHERE rc.region_code = v.region_code);

COMMIT;

-- Rollback:
-- DELETE FROM region_configs WHERE region_code IN ('yaransk','sanchursk','kiknur');
-- DELETE FROM regions WHERE code IN ('yaransk','sanchursk','kiknur');
