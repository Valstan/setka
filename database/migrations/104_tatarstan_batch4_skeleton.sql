-- 104: skeleton for the fourth Tatarstan batch: zelenodolsk (Зеленодольский), laishevo
-- (Лаишевский), elabuga (Елабужский). Same pattern as 101/102/103: INACTIVE regions with
-- vk_group_id NULL — the "- ИНФО" groups are created after this migration. Additive and reversible.
--
-- Why these three (owner's order 2026-09-17: «ещё три района в Татарстане»). Chosen by
-- adjacency to the live patch, each one touches an already-active district. Borders checked
-- against ru.wikipedia on 2026-09-17, not from memory:
--   Зеленодольский — NE: Высокогорский (ours), E: Казань, SE: Верхнеуслонский, S: Кайбицкий,
--                    W: Чувашия, N: Марий Эл. 169k people (г. Зеленодольск ~99k) — the largest
--                    district we have taken so far; closes the western edge of the patch.
--   Лаишевский     — by land: Казань, Пестречинский (ours), Рыбно-Слободский (ours); across the
--                    Kuibyshev reservoir: Верхнеуслонский, Камско-Устьинский, Алексеевский,
--                    Спасский. 72k, Kazan's south-eastern suburban belt (Столбище, Усады, Сокуры).
--   Елабужский     — Мамадышский (ours), Менделеевский, Нижнекамский, Тукаевский, г.о. Набережные
--                    Челны, Удмуртия. 85k (г. Елабуга ~74k); first step east of the Vyatka.
-- Together with the ten live ones this is one contiguous patch from Зеленодольск to Елабуга.
--
-- Branding: full 9-theme Kirov template, «района» (Tatarstan has municipal districts).
--
-- Localities: administrative centres of сельские/городские поселения per ru.wikipedia, ten per
-- district. Deliberately left out:
--   • Зеленодольский: «Нурлаты» (stem collides with г. Нурлат), «Октябрьский», «Новопольский»,
--     «Молвино», «Никольское»-class generic names; «Бело-Безводное» replaced by «Раифа» (the
--     monastery name is what people actually write).
--   • Лаишевский: «Орёл» (collides with our Kirov `orlov` district), «Никольское»,
--     «Александровское» (generic), «Усады» (already in vysokaya_gora's list — same-name village
--     in two neighbouring districts, keep it with the one that took it first).
--   • Елабужский: «Альметьево» (stem collides with г. Альметьевск), «Яковлево» (generic).
--   • «Васильево» is generic Russia-wide but is an 8k-people town here — kept, the regional
--     marker filter at scan time handles the rest.

BEGIN;

INSERT INTO regions (code, name, vk_group_id, kind, parent_region_id, is_active)
SELECT v.code, v.name, NULL, 'raion', (SELECT id FROM regions WHERE code = 'tatarstan_obl'), FALSE
FROM (VALUES
    ('zelenodolsk', 'ЗЕЛЕНОДОЛЬСК - ИНФО'),
    ('laishevo', 'ЛАИШЕВО - ИНФО'),
    ('elabuga', 'ЕЛАБУГА - ИНФО')
) AS v(code, name)
WHERE NOT EXISTS (SELECT 1 FROM regions r WHERE r.code = v.code);

INSERT INTO region_configs (region_code, zagolovki, heshteg_local, text_post_maxsize_simbols, localities, created_at, updated_at)
SELECT * FROM (VALUES
(
    'zelenodolsk',
    '{
        "novost": "Новости Зеленодольского района:",
        "reklama": "Объявления Зеленодольского района:",
        "kultura": "Культура Зеленодольского района:",
        "sport": "Спорт Зеленодольского района:",
        "admin": "Власть и общество Зеленодольского района:",
        "union": "Молодёжь и образование Зеленодольского района:",
        "detsad": "Детские сады Зеленодольского района:",
        "sosed": "Происшествия Зеленодольского района:",
        "addons": "Зеленодольский район — также:"
    }'::json,
    '{"raicentr": "Зеленодольск"}'::json,
    4096,
    '["Зеленодольск", "Васильево", "Нижние Вязовые", "Осиново", "Айша", "Большие Ключи", "Свияжск", "Раифа", "Большие Ачасыры", "Кугушево"]'::json,
    (now() AT TIME ZONE 'utc'), (now() AT TIME ZONE 'utc')
),
(
    'laishevo',
    '{
        "novost": "Новости Лаишевского района:",
        "reklama": "Объявления Лаишевского района:",
        "kultura": "Культура Лаишевского района:",
        "sport": "Спорт Лаишевского района:",
        "admin": "Власть и общество Лаишевского района:",
        "union": "Молодёжь и образование Лаишевского района:",
        "detsad": "Детские сады Лаишевского района:",
        "sosed": "Происшествия Лаишевского района:",
        "addons": "Лаишевский район — также:"
    }'::json,
    '{"raicentr": "Лаишево"}'::json,
    4096,
    '["Лаишево", "Столбище", "Сокуры", "Габишево", "Песчаные Ковали", "Нармонка", "Державино", "Атабаево", "Ташкирмень", "Малые Кабаны"]'::json,
    (now() AT TIME ZONE 'utc'), (now() AT TIME ZONE 'utc')
),
(
    'elabuga',
    '{
        "novost": "Новости Елабужского района:",
        "reklama": "Объявления Елабужского района:",
        "kultura": "Культура Елабужского района:",
        "sport": "Спорт Елабужского района:",
        "admin": "Власть и общество Елабужского района:",
        "union": "Молодёжь и образование Елабужского района:",
        "detsad": "Детские сады Елабужского района:",
        "sosed": "Происшествия Елабужского района:",
        "addons": "Елабужский район — также:"
    }'::json,
    '{"raicentr": "Елабуга"}'::json,
    4096,
    '["Елабуга", "Танайка", "Бехтерево", "Костенеево", "Морты", "Лекарево", "Поспелово", "Большое Елово", "Старый Куклюк", "Большая Качка"]'::json,
    (now() AT TIME ZONE 'utc'), (now() AT TIME ZONE 'utc')
)
) AS v(region_code, zagolovki, heshteg_local, text_post_maxsize_simbols, localities, created_at, updated_at)
WHERE NOT EXISTS (SELECT 1 FROM region_configs rc WHERE rc.region_code = v.region_code);

-- Соседи. Новым — только коды, существующие в `regions` (Казань, Верхнеуслонский, Кайбицкий,
-- Менделеевский, Нижнекамский, Тукаевский и т.д. в сети не заведены). Уже живым — текущее
-- значение с прода (снято 2026-09-17) ПЛЮС новые коды, по алфавиту.
UPDATE regions SET neighbors = 'vysokaya_gora' WHERE code = 'zelenodolsk';
UPDATE regions SET neighbors = 'pestretsy,rybnaya_sloboda' WHERE code = 'laishevo';
UPDATE regions SET neighbors = 'mamadysh' WHERE code = 'elabuga';
UPDATE regions SET neighbors = 'arsk,atnya,pestretsy,zelenodolsk' WHERE code = 'vysokaya_gora';
UPDATE regions SET neighbors = 'arsk,laishevo,rybnaya_sloboda,tyulyachi,vysokaya_gora' WHERE code = 'pestretsy';
UPDATE regions SET neighbors = 'laishevo,mamadysh,pestretsy,saby,tyulyachi' WHERE code = 'rybnaya_sloboda';
UPDATE regions SET neighbors = 'elabuga,kukmor,rybnaya_sloboda,saby,tyulyachi' WHERE code = 'mamadysh';

COMMIT;

-- Проверка после применения:
--   SELECT code, name, is_active, vk_group_id, neighbors FROM regions
--   WHERE code IN ('zelenodolsk','laishevo','elabuga','vysokaya_gora','pestretsy','rybnaya_sloboda','mamadysh')
--   ORDER BY code;
-- Симметричность (пустая выдача = все связи двусторонние):
--   SELECT a.code, b.code FROM regions a JOIN regions b
--     ON b.code = ANY(string_to_array(a.neighbors, ','))
--   WHERE NOT (a.code = ANY(string_to_array(coalesce(b.neighbors,''), ',')));
--
-- Rollback (возврат к состоянию после 103, снятому с прода 2026-09-17):
-- UPDATE regions SET neighbors = 'arsk,atnya,pestretsy' WHERE code = 'vysokaya_gora';
-- UPDATE regions SET neighbors = 'arsk,rybnaya_sloboda,tyulyachi,vysokaya_gora' WHERE code = 'pestretsy';
-- UPDATE regions SET neighbors = 'mamadysh,pestretsy,saby,tyulyachi' WHERE code = 'rybnaya_sloboda';
-- UPDATE regions SET neighbors = 'kukmor,rybnaya_sloboda,saby,tyulyachi' WHERE code = 'mamadysh';
-- DELETE FROM region_configs WHERE region_code IN ('zelenodolsk','laishevo','elabuga');
-- DELETE FROM regions WHERE code IN ('zelenodolsk','laishevo','elabuga');
