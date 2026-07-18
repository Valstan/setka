-- 066: skeleton for batch-6 new raions of Kirov oblast: darovskoy, svecha, shabalino.
-- Same pattern as 061-065: INACTIVE regions with vk_group_id NULL — main "- ИНФО"
-- groups created by the owner after 2026-07-20. Additive and reversible.
-- Note: shabalino raion center is пгт Ленинское.

BEGIN;

INSERT INTO regions (code, name, vk_group_id, kind, parent_region_id, is_active)
SELECT v.code, v.name, NULL, 'raion', (SELECT id FROM regions WHERE code = 'kirov_obl'), FALSE
FROM (VALUES
    ('darovskoy', 'ДАРОВСКОЙ - ИНФО'),
    ('svecha',    'СВЕЧА - ИНФО'),
    ('shabalino', 'ШАБАЛИНО - ИНФО')
) AS v(code, name)
WHERE NOT EXISTS (SELECT 1 FROM regions r WHERE r.code = v.code);

INSERT INTO region_configs (region_code, zagolovki, heshteg_local, text_post_maxsize_simbols, localities, created_at, updated_at)
SELECT * FROM (VALUES
(
    'darovskoy',
    '{
        "novost": "Новости Даровского района:",
        "reklama": "Объявления Даровского района:",
        "kultura": "Культура Даровского района:",
        "sport": "Спорт Даровского района:",
        "admin": "Власть и общество Даровского района:",
        "union": "Молодёжь и образование Даровского:",
        "detsad": "Детские сады Даровского:",
        "sosed": "Происшествия Даровского района:",
        "addons": "Даровской район — также:"
    }'::json,
    '{"raicentr": "Даровской"}'::json,
    4096,
    '["Даровской", "Верховонданка", "Вонданка", "Кобра", "Красное", "Пиксур", "Бобровы", "Окатьево", "Торопово", "Александровское", "Суборь", "Ивановка", "Кривецкая", "Знаменка"]'::json,
    now(), now()
),
(
    'svecha',
    '{
        "novost": "Новости Свечинского округа:",
        "reklama": "Объявления Свечинского округа:",
        "kultura": "Культура Свечинского округа:",
        "sport": "Спорт Свечинского округа:",
        "admin": "Власть и общество Свечинского округа:",
        "union": "Молодёжь и образование Свечи:",
        "detsad": "Детские сады Свечи:",
        "sosed": "Происшествия Свечинского округа:",
        "addons": "Свечинский округ — также:"
    }'::json,
    '{"raicentr": "Свеча"}'::json,
    4096,
    '["Свеча", "Юма", "Круглыжи", "Ацвеж", "Благовещенское", "Шмелево", "Самоулки", "Еременки", "Октябрьское", "Успенское"]'::json,
    now(), now()
),
(
    'shabalino',
    '{
        "novost": "Новости Шабалинского района:",
        "reklama": "Объявления Шабалинского района:",
        "kultura": "Культура Шабалинского района:",
        "sport": "Спорт Шабалинского района:",
        "admin": "Власть и общество Шабалинского района:",
        "union": "Молодёжь и образование Ленинского:",
        "detsad": "Детские сады Ленинского:",
        "sosed": "Происшествия Шабалинского района:",
        "addons": "Шабалинский район — также:"
    }'::json,
    '{"raicentr": "Ленинское"}'::json,
    4096,
    '["Ленинское", "Новотроицкое", "Черновское", "Высокораменское", "Гостовский", "Архангельское", "Прокопьевское", "Николаевское", "Соловецкое", "Колосово", "Крутики"]'::json,
    now(), now()
)
) AS v(region_code, zagolovki, heshteg_local, text_post_maxsize_simbols, localities, created_at, updated_at)
WHERE NOT EXISTS (SELECT 1 FROM region_configs rc WHERE rc.region_code = v.region_code);

COMMIT;

-- Rollback:
-- DELETE FROM region_configs WHERE region_code IN ('darovskoy','svecha','shabalino');
-- DELETE FROM regions WHERE code IN ('darovskoy','svecha','shabalino');
