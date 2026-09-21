-- 106: skeleton for the fifth Tatarstan batch: mendeleevsk (Менделеевский), nizhnekamsk
-- (Нижнекамский), verhniy_uslon (Верхнеуслонский). Same pattern as 101–104: INACTIVE regions
-- with vk_group_id NULL — the "- ИНФО" groups are created after this migration. Additive and
-- reversible.
--
-- Why these three (owner's order 2026-09-21: «три района из Татарстана подсоедини»). Chosen
-- by adjacency to the live patch, each one touches an already-active district. Borders checked
-- against ru.wikipedia on 2026-09-21, not from memory:
--   Менделеевский   — W: Елабужский (ours), S: Тукаевский, E: Агрызский, N: Удмуртия. 30k
--                     (г. Менделеевск ~23k). Continues the eastern edge past Елабуга.
--   Нижнекамский    — N: Елабужский и Мамадышский (both ours, across the Kama), E: Тукаевский и
--                     Заинский, SE: Альметьевский, S: Новошешминский, W: Чистопольский. 276k
--                     (г. Нижнекамск ~241k) — the largest district taken so far; the pool will be
--                     trimmed at seed time the way Зеленодольск was.
--   Верхнеуслонский — N: Зеленодольский (ours), E: Лаишевский (ours) и Казань, S: Камско-
--                     Устьинский, Апастовский, Кайбицкий. 17.5k; Иннополис is inside it.
--                     Closes the gap between Зеленодольск and Лаишево on the right bank.
-- Тукаевский was considered and skipped: its centre is Набережные Челны, which is a city
-- district outside the район — a «ТУКАЕВСКИЙ - ИНФО» group would have no town to anchor to.
--
-- Branding: full 9-theme Kirov template, «района» (Tatarstan has municipal districts).
--
-- Localities: administrative centres of сельские/городские поселения per ru.wikipedia, ten per
-- district. Deliberately left out:
--   • Нижнекамский: «Прости» (stem collides with the word «просто»), «Красный Ключ» and
--     «Трудовой»/«Благодатная» (generic Russia-wide), «Верхние Челны» (collides with
--     Набережные Челны stem).
--   • Верхнеуслонский: «Майдан», «Октябрьский», «Канаш» (a Chuvash city), «имени М. Вахитова»
--     (unusable as a search term).
--   • Менделеевский: «Брюшли», «Мунайка», «Псеево», «Тойгузино», «Енабердино» (tiny, no VK
--     footprint expected) — ten kept are the larger villages.

BEGIN;

INSERT INTO regions (code, name, vk_group_id, kind, parent_region_id, is_active)
SELECT v.code, v.name, NULL, 'raion', (SELECT id FROM regions WHERE code = 'tatarstan_obl'), FALSE
FROM (VALUES
    ('mendeleevsk', 'МЕНДЕЛЕЕВСК - ИНФО'),
    ('nizhnekamsk', 'НИЖНЕКАМСК - ИНФО'),
    ('verhniy_uslon', 'ВЕРХНИЙ УСЛОН - ИНФО')
) AS v(code, name)
WHERE NOT EXISTS (SELECT 1 FROM regions r WHERE r.code = v.code);

INSERT INTO region_configs (region_code, zagolovki, heshteg_local, text_post_maxsize_simbols, localities, created_at, updated_at)
SELECT * FROM (VALUES
(
    'mendeleevsk',
    '{
        "novost": "Новости Менделеевского района:",
        "reklama": "Объявления Менделеевского района:",
        "kultura": "Культура Менделеевского района:",
        "sport": "Спорт Менделеевского района:",
        "admin": "Власть и общество Менделеевского района:",
        "union": "Молодёжь и образование Менделеевского района:",
        "detsad": "Детские сады Менделеевского района:",
        "sosed": "Происшествия Менделеевского района:",
        "addons": "Менделеевский район — также:"
    }'::json,
    '{"raicentr": "Менделеевск"}'::json,
    4096,
    '["Менделеевск", "Бизяки", "Ижевка", "Тихоново", "Тураево", "Камаево", "Монашево", "Старое Гришкино", "Татарские Челны", "Абалачи"]'::json,
    (now() AT TIME ZONE 'utc'), (now() AT TIME ZONE 'utc')
),
(
    'nizhnekamsk',
    '{
        "novost": "Новости Нижнекамского района:",
        "reklama": "Объявления Нижнекамского района:",
        "kultura": "Культура Нижнекамского района:",
        "sport": "Спорт Нижнекамского района:",
        "admin": "Власть и общество Нижнекамского района:",
        "union": "Молодёжь и образование Нижнекамского района:",
        "detsad": "Детские сады Нижнекамского района:",
        "sosed": "Происшествия Нижнекамского района:",
        "addons": "Нижнекамский район — также:"
    }'::json,
    '{"raicentr": "Нижнекамск"}'::json,
    4096,
    '["Нижнекамск", "Камские Поляны", "Большое Афанасово", "Шереметьевка", "Сухарево", "Шингальчи", "Каенлы", "Старошешминск", "Кармалы", "Елантово"]'::json,
    (now() AT TIME ZONE 'utc'), (now() AT TIME ZONE 'utc')
),
(
    'verhniy_uslon',
    '{
        "novost": "Новости Верхнеуслонского района:",
        "reklama": "Объявления Верхнеуслонского района:",
        "kultura": "Культура Верхнеуслонского района:",
        "sport": "Спорт Верхнеуслонского района:",
        "admin": "Власть и общество Верхнеуслонского района:",
        "union": "Молодёжь и образование Верхнеуслонского района:",
        "detsad": "Детские сады Верхнеуслонского района:",
        "sosed": "Происшествия Верхнеуслонского района:",
        "addons": "Верхнеуслонский район — также:"
    }'::json,
    '{"raicentr": "Верхний Услон"}'::json,
    4096,
    '["Верхний Услон", "Иннополис", "Нижний Услон", "Печищи", "Шеланга", "Куралово", "Введенская Слобода", "Набережные Моркваши", "Кильдеево", "Ямбулатово"]'::json,
    (now() AT TIME ZONE 'utc'), (now() AT TIME ZONE 'utc')
)
) AS v(region_code, zagolovki, heshteg_local, text_post_maxsize_simbols, localities, created_at, updated_at)
WHERE NOT EXISTS (SELECT 1 FROM region_configs rc WHERE rc.region_code = v.region_code);

-- Соседи. Новым — только коды, существующие в `regions` (Тукаевский, Агрызский, Заинский,
-- Чистопольский, Кайбицкий и т.д. в сети не заведены). Уже живым — текущее значение с прода
-- (снято 2026-09-21) ПЛЮС новые коды, по алфавиту.
UPDATE regions SET neighbors = 'elabuga' WHERE code = 'mendeleevsk';
UPDATE regions SET neighbors = 'elabuga,mamadysh' WHERE code = 'nizhnekamsk';
UPDATE regions SET neighbors = 'laishevo,zelenodolsk' WHERE code = 'verhniy_uslon';
UPDATE regions SET neighbors = 'mamadysh,mendeleevsk,nizhnekamsk' WHERE code = 'elabuga';
UPDATE regions SET neighbors = 'elabuga,kukmor,nizhnekamsk,rybnaya_sloboda,saby,tyulyachi' WHERE code = 'mamadysh';
UPDATE regions SET neighbors = 'pestretsy,rybnaya_sloboda,verhniy_uslon' WHERE code = 'laishevo';
UPDATE regions SET neighbors = 'verhniy_uslon,vysokaya_gora' WHERE code = 'zelenodolsk';

COMMIT;

-- Проверка после применения:
--   SELECT code, name, is_active, vk_group_id, neighbors FROM regions
--   WHERE code IN ('mendeleevsk','nizhnekamsk','verhniy_uslon','elabuga','mamadysh','laishevo','zelenodolsk')
--   ORDER BY code;
-- Симметричность (пустая выдача = все связи двусторонние):
--   SELECT a.code, b.code FROM regions a JOIN regions b
--     ON b.code = ANY(string_to_array(a.neighbors, ','))
--   WHERE NOT (a.code = ANY(string_to_array(coalesce(b.neighbors,''), ',')));
--
-- Rollback (возврат к состоянию после 104, снятому с прода 2026-09-21):
-- UPDATE regions SET neighbors = 'mamadysh' WHERE code = 'elabuga';
-- UPDATE regions SET neighbors = 'elabuga,kukmor,rybnaya_sloboda,saby,tyulyachi' WHERE code = 'mamadysh';
-- UPDATE regions SET neighbors = 'pestretsy,rybnaya_sloboda' WHERE code = 'laishevo';
-- UPDATE regions SET neighbors = 'vysokaya_gora' WHERE code = 'zelenodolsk';
-- DELETE FROM region_configs WHERE region_code IN ('mendeleevsk','nizhnekamsk','verhniy_uslon');
-- DELETE FROM regions WHERE code IN ('mendeleevsk','nizhnekamsk','verhniy_uslon');
