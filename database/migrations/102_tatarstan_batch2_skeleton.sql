-- 102: skeleton for the second Tatarstan batch: tyulyachi (Тюлячинский), mamadysh (Мамадышский),
-- atnya (Атнинский). Same pattern as 101 (saby/arsk): INACTIVE regions with vk_group_id NULL —
-- the "- ИНФО" groups are created after this migration. Additive and reversible.
--
-- Why these three (owner's order 2026-09-12: «продолжать захват республики» — a contiguous cluster,
-- not scattered dots). Borders checked against ru.wikipedia on 2026-09-12, not from memory:
--   Тюлячинский — N: Сабинский, E: Мамадышский, Рыбно-Слободский, SW: Пестречинский, NW: Арский.
--                  The only district touching THREE of ours (saby, arsk, mamadysh).
--   Мамадышский  — N: Кукморский, W: Тюлячинский и Сабинский, E: Елабужский, NE: Удмуртия.
--                  39.7k people — the largest of the batch; town-centred (г. Мамадыш).
--   Атнинский    — Арский, Высокогорский, Моркинский (Марий Эл). Smallest (12.5k), Arsk's neighbour.
-- Together with saby/arsk/bal/kukmor this makes one patch Балтаси–Кукмор–Сабы–Арск–Тюлячи–
-- Мамадыш–Атня; every new region has ≥1 already-active neighbour, so the neighbour exchange
-- works from the first day.
--
-- Branding: full 9-theme Kirov template, «района» (Tatarstan has municipal districts).
-- raicentr goes into hashtags (#<raicentr>, «спорт<raicentr>»), so no spaces:
-- «Большая Атня» → «Атня» (locals say «в Атне»); «Тюлячи», «Мамадыш» as is.
-- Localities: the ten largest settlement centres per ru.wikipedia (сельские поселения list);
-- generic names left out on purpose — «посёлок совхоза „Мамадышский“», «посёлок Зверосовхоза»
-- (would match everywhere), «Зюри» in Мамадышский (collides with «Старые Зюри» in Тюлячинский).
--
-- Neighbours: symmetric; only codes that exist in `regions`. saby/arsk/kukmor get the new codes
-- APPENDED to their current values (read from prod 2026-09-12). Sources of neighbours resolve
-- with `is_active = TRUE AND vk_group_id IS NOT NULL`, so for the active ones this is a no-op
-- until the new regions are activated.

BEGIN;

INSERT INTO regions (code, name, vk_group_id, kind, parent_region_id, is_active)
SELECT v.code, v.name, NULL, 'raion', (SELECT id FROM regions WHERE code = 'tatarstan_obl'), FALSE
FROM (VALUES
    ('tyulyachi', 'ТЮЛЯЧИ - ИНФО'),
    ('mamadysh', 'МАМАДЫШ - ИНФО'),
    ('atnya', 'АТНЯ - ИНФО')
) AS v(code, name)
WHERE NOT EXISTS (SELECT 1 FROM regions r WHERE r.code = v.code);

INSERT INTO region_configs (region_code, zagolovki, heshteg_local, text_post_maxsize_simbols, localities, created_at, updated_at)
SELECT * FROM (VALUES
(
    'tyulyachi',
    '{
        "novost": "Новости Тюлячинского района:",
        "reklama": "Объявления Тюлячинского района:",
        "kultura": "Культура Тюлячинского района:",
        "sport": "Спорт Тюлячинского района:",
        "admin": "Власть и общество Тюлячинского района:",
        "union": "Молодёжь и образование Тюлячинского района:",
        "detsad": "Детские сады Тюлячинского района:",
        "sosed": "Происшествия Тюлячинского района:",
        "addons": "Тюлячинский район — также:"
    }'::json,
    '{"raicentr": "Тюлячи"}'::json,
    4096,
    '["Тюлячи", "Абди", "Айдарово", "Алан", "Баландыш", "Большие Метески", "Большая Мёша", "Большие Нырси", "Старые Зюри", "Шадки"]'::json,
    (now() AT TIME ZONE 'utc'), (now() AT TIME ZONE 'utc')
),
(
    'mamadysh',
    '{
        "novost": "Новости Мамадышского района:",
        "reklama": "Объявления Мамадышского района:",
        "kultura": "Культура Мамадышского района:",
        "sport": "Спорт Мамадышского района:",
        "admin": "Власть и общество Мамадышского района:",
        "union": "Молодёжь и образование Мамадыша:",
        "detsad": "Детские сады Мамадыша:",
        "sosed": "Происшествия Мамадышского района:",
        "addons": "Мамадышский район — также:"
    }'::json,
    '{"raicentr": "Мамадыш"}'::json,
    4096,
    '["Мамадыш", "Нижний Таканыш", "Соколка", "Омары", "Усали", "Кемеш-Куль", "Малые Кирмени", "Средние Кирмени", "Нижняя Ошма", "Верхняя Ошма"]'::json,
    (now() AT TIME ZONE 'utc'), (now() AT TIME ZONE 'utc')
),
(
    'atnya',
    '{
        "novost": "Новости Атнинского района:",
        "reklama": "Объявления Атнинского района:",
        "kultura": "Культура Атнинского района:",
        "sport": "Спорт Атнинского района:",
        "admin": "Власть и общество Атнинского района:",
        "union": "Молодёжь и образование Атнинского района:",
        "detsad": "Детские сады Атнинского района:",
        "sosed": "Происшествия Атнинского района:",
        "addons": "Атнинский район — также:"
    }'::json,
    '{"raicentr": "Атня"}'::json,
    4096,
    '["Большая Атня", "Большой Менгер", "Кубян", "Кунгер", "Кулле-Кими", "Нижняя Береске", "Новые Шаши", "Коморгузя", "Верхняя Серда", "Кшклово"]'::json,
    (now() AT TIME ZONE 'utc'), (now() AT TIME ZONE 'utc')
)
) AS v(region_code, zagolovki, heshteg_local, text_post_maxsize_simbols, localities, created_at, updated_at)
WHERE NOT EXISTS (SELECT 1 FROM region_configs rc WHERE rc.region_code = v.region_code);

UPDATE regions SET neighbors = 'arsk,mamadysh,saby' WHERE code = 'tyulyachi';
UPDATE regions SET neighbors = 'kukmor,saby,tyulyachi' WHERE code = 'mamadysh';
UPDATE regions SET neighbors = 'arsk' WHERE code = 'atnya';
UPDATE regions SET neighbors = 'arsk,bal,kukmor,mamadysh,tyulyachi' WHERE code = 'saby';
UPDATE regions SET neighbors = 'atnya,bal,saby,tyulyachi' WHERE code = 'arsk';
UPDATE regions SET neighbors = 'bal,klz,mamadysh,mi,saby,ur,vp' WHERE code = 'kukmor';

COMMIT;

-- Проверка после применения:
--   SELECT code, name, is_active, vk_group_id, neighbors FROM regions
--   WHERE code IN ('tyulyachi','mamadysh','atnya','saby','arsk','kukmor') ORDER BY code;
-- Симметричность (пустая выдача = все связи двусторонние):
--   SELECT a.code, b.code FROM regions a JOIN regions b
--     ON b.code = ANY(string_to_array(a.neighbors, ','))
--   WHERE NOT (a.code = ANY(string_to_array(coalesce(b.neighbors,''), ',')));
--
-- Rollback:
-- UPDATE regions SET neighbors = 'arsk,bal,kukmor' WHERE code = 'saby';
-- UPDATE regions SET neighbors = 'bal,saby' WHERE code = 'arsk';
-- UPDATE regions SET neighbors = 'bal,klz,mi,saby,ur,vp' WHERE code = 'kukmor';
-- DELETE FROM region_configs WHERE region_code IN ('tyulyachi','mamadysh','atnya');
-- DELETE FROM regions WHERE code IN ('tyulyachi','mamadysh','atnya');
