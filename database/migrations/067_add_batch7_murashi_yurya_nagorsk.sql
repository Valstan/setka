-- 067: skeleton for batch-7 new raions of Kirov oblast: murashi, yurya, nagorsk.
-- Same pattern as 061-066: INACTIVE regions with vk_group_id NULL — main "- ИНФО"
-- groups created by the owner after 2026-07-20. Additive and reversible.

BEGIN;

INSERT INTO regions (code, name, vk_group_id, kind, parent_region_id, is_active)
SELECT v.code, v.name, NULL, 'raion', (SELECT id FROM regions WHERE code = 'kirov_obl'), FALSE
FROM (VALUES
    ('murashi', 'МУРАШИ - ИНФО'),
    ('yurya',   'ЮРЬЯ - ИНФО'),
    ('nagorsk', 'НАГОРСК - ИНФО')
) AS v(code, name)
WHERE NOT EXISTS (SELECT 1 FROM regions r WHERE r.code = v.code);

INSERT INTO region_configs (region_code, zagolovki, heshteg_local, text_post_maxsize_simbols, localities, created_at, updated_at)
SELECT * FROM (VALUES
(
    'murashi',
    '{
        "novost": "Новости Мурашинского округа:",
        "reklama": "Объявления Мурашинского округа:",
        "kultura": "Культура Мурашинского округа:",
        "sport": "Спорт Мурашинского округа:",
        "admin": "Власть и общество Мурашинского округа:",
        "union": "Молодёжь и образование Мурашей:",
        "detsad": "Детские сады Мурашей:",
        "sosed": "Происшествия Мурашинского округа:",
        "addons": "Мурашинский округ — также:"
    }'::json,
    '{"raicentr": "Мураши"}'::json,
    4096,
    '["Мураши", "Безбожник", "Октябрьский", "Паломохино", "Боровица", "Верхораменье", "Даниловка", "Шубрюг", "Волосница", "Тылай"]'::json,
    now(), now()
),
(
    'yurya',
    '{
        "novost": "Новости Юрьянского района:",
        "reklama": "Объявления Юрьянского района:",
        "kultura": "Культура Юрьянского района:",
        "sport": "Спорт Юрьянского района:",
        "admin": "Власть и общество Юрьянского района:",
        "union": "Молодёжь и образование Юрьи:",
        "detsad": "Детские сады Юрьи:",
        "sosed": "Происшествия Юрьянского района:",
        "addons": "Юрьянский район — также:"
    }'::json,
    '{"raicentr": "Юрья"}'::json,
    4096,
    '["Юрья", "Мурыгино", "Загарье", "Верховино", "Верходворье", "Великорецкое", "Медяны", "Пышак", "Ложкари", "Гирсово", "Монастырское", "Подгорцы", "Северный"]'::json,
    now(), now()
),
(
    'nagorsk',
    '{
        "novost": "Новости Нагорского округа:",
        "reklama": "Объявления Нагорского округа:",
        "kultura": "Культура Нагорского округа:",
        "sport": "Спорт Нагорского округа:",
        "admin": "Власть и общество Нагорского округа:",
        "union": "Молодёжь и образование Нагорска:",
        "detsad": "Детские сады Нагорска:",
        "sosed": "Происшествия Нагорского округа:",
        "addons": "Нагорский округ — также:"
    }'::json,
    '{"raicentr": "Нагорск"}'::json,
    4096,
    '["Нагорск", "Синегорье", "Чеглаки", "Кобра", "Метелево", "Мулино", "Кошулино", "Слободка", "Николаевское"]'::json,
    now(), now()
)
) AS v(region_code, zagolovki, heshteg_local, text_post_maxsize_simbols, localities, created_at, updated_at)
WHERE NOT EXISTS (SELECT 1 FROM region_configs rc WHERE rc.region_code = v.region_code);

COMMIT;

-- Rollback:
-- DELETE FROM region_configs WHERE region_code IN ('murashi','yurya','nagorsk');
-- DELETE FROM regions WHERE code IN ('murashi','yurya','nagorsk');
