-- 103: skeleton for the third Tatarstan batch: vysokaya_gora (Высокогорский),
-- pestretsy (Пестречинский), rybnaya_sloboda (Рыбно-Слободский). Same pattern as 101/102:
-- INACTIVE regions with vk_group_id NULL — the "- ИНФО" groups are created after this
-- migration. Additive and reversible.
--
-- Why these three (owner's order 2026-09-14, continuing «захват республики»; the cluster was
-- already named as next in SESSION_HANDOFF 12.09). Borders checked against ru.wikipedia on
-- 2026-09-14, not from memory:
--   Высокогорский  — W: Зеленодольский, E: Арский и Атнинский, S: Пестречинский и Казань,
--                    N: Марий Эл. Touches TWO already-active ones (arsk, atnya) and closes the
--                    western edge of the patch. 56.0k people, the largest of the batch.
--   Пестречинский  — N: Высокогорский и Арский, E: Тюлячинский, S: Рыбно-Слободский и
--                    Лаишевский, W: Казань. 62.1k and growing fast (Казань's suburban belt).
--   Рыбно-Слободский — N: Пестречинский, Тюлячинский, Сабинский; W: Лаишевский; E: Мамадышский;
--                    S: Чистопольский и Алексеевский (за Камой). 23.9k. Closes the southern edge.
-- Together with the seven live ones this makes a single contiguous patch Атня–Высокая Гора–
-- Пестрецы–Арск–Балтаси–Кукмор–Сабы–Тюлячи–Мамадыш–Рыбная Слобода; every new region has ≥2
-- already-active neighbours, so the neighbour exchange works from the first day.
--
-- Branding: full 9-theme Kirov template, «района» (Tatarstan has municipal districts).
--
-- 🔸 Двухсловные райцентры. «Высокая Гора» и «Рыбная Слобода» — из двух слов, а `raicentr`
--    уходит в комбинированный тег сводки сырым («спортВысокая Гора»). Это НЕ новая проблема и
--    не повод выдумывать односложную форму: ровно так с 2026-07 живёт `belholunitsa`
--    («Белая Холуница»), проверено на проде 2026-09-14. Настоящее имя важнее косметики тега —
--    оно же идёт в `region_display_name`. Отдельный тег района ставится через
--    `regions.local_hashtags` (там `normalize_tag` сам делает «белая_холуница»), и к сводке
--    отношения не имеет. Хвост записан в PENDING_FOLLOWUPS.
--
-- Localities: административные центры сельских поселений по ru.wikipedia. Намеренно выпущены:
--   • «посёлок Бирюлинского зверосовхоза», «Дачное», «Берёзка», «Полянка», «Масловка» —
--     родовые имена, совпадут где угодно;
--   • «Богородское» (Пестречинский, 19.5k — крупнейшее в районе) — ТОЧНОЕ совпадение с именем
--     нашего же кировского района `bogorodskoe`. Ловушка того же класса, что «Зюри» в 102:
--     посты Богородского района Кировской области утекли бы в ленту Пестрецов. Вернуть можно
--     только вместе с дизамбигуацией по региону.

BEGIN;

INSERT INTO regions (code, name, vk_group_id, kind, parent_region_id, is_active)
SELECT v.code, v.name, NULL, 'raion', (SELECT id FROM regions WHERE code = 'tatarstan_obl'), FALSE
FROM (VALUES
    ('vysokaya_gora', 'ВЫСОКАЯ ГОРА - ИНФО'),
    ('pestretsy', 'ПЕСТРЕЦЫ - ИНФО'),
    ('rybnaya_sloboda', 'РЫБНАЯ СЛОБОДА - ИНФО')
) AS v(code, name)
WHERE NOT EXISTS (SELECT 1 FROM regions r WHERE r.code = v.code);

INSERT INTO region_configs (region_code, zagolovki, heshteg_local, text_post_maxsize_simbols, localities, created_at, updated_at)
SELECT * FROM (VALUES
(
    'vysokaya_gora',
    '{
        "novost": "Новости Высокогорского района:",
        "reklama": "Объявления Высокогорского района:",
        "kultura": "Культура Высокогорского района:",
        "sport": "Спорт Высокогорского района:",
        "admin": "Власть и общество Высокогорского района:",
        "union": "Молодёжь и образование Высокогорского района:",
        "detsad": "Детские сады Высокогорского района:",
        "sosed": "Происшествия Высокогорского района:",
        "addons": "Высокогорский район — также:"
    }'::json,
    '{"raicentr": "Высокая Гора"}'::json,
    4096,
    '["Высокая Гора", "Дубъязы", "Куркачи", "Айбаш", "Альдермыш", "Усады", "Чепчуги", "Шапши", "Ямашурма", "Мемдель"]'::json,
    (now() AT TIME ZONE 'utc'), (now() AT TIME ZONE 'utc')
),
(
    'pestretsy',
    '{
        "novost": "Новости Пестречинского района:",
        "reklama": "Объявления Пестречинского района:",
        "kultura": "Культура Пестречинского района:",
        "sport": "Спорт Пестречинского района:",
        "admin": "Власть и общество Пестречинского района:",
        "union": "Молодёжь и образование Пестречинского района:",
        "detsad": "Детские сады Пестречинского района:",
        "sosed": "Происшествия Пестречинского района:",
        "addons": "Пестречинский район — также:"
    }'::json,
    '{"raicentr": "Пестрецы"}'::json,
    4096,
    '["Пестрецы", "Старое Шигалеево", "Кулаево", "Шали", "Ленино-Кокушкино", "Кощаково", "Пановка", "Кряш-Серда", "Янцевары", "Отар-Дубровка"]'::json,
    (now() AT TIME ZONE 'utc'), (now() AT TIME ZONE 'utc')
),
(
    'rybnaya_sloboda',
    '{
        "novost": "Новости Рыбно-Слободского района:",
        "reklama": "Объявления Рыбно-Слободского района:",
        "kultura": "Культура Рыбно-Слободского района:",
        "sport": "Спорт Рыбно-Слободского района:",
        "admin": "Власть и общество Рыбно-Слободского района:",
        "union": "Молодёжь и образование Рыбно-Слободского района:",
        "detsad": "Детские сады Рыбно-Слободского района:",
        "sosed": "Происшествия Рыбно-Слободского района:",
        "addons": "Рыбно-Слободский район — также:"
    }'::json,
    '{"raicentr": "Рыбная Слобода"}'::json,
    4096,
    '["Рыбная Слобода", "Кутлу-Букаш", "Шумбут", "Большая Елга", "Анатыш", "Бетьки", "Урахча", "Юлсубино", "Корноухово", "Шеморбаш"]'::json,
    (now() AT TIME ZONE 'utc'), (now() AT TIME ZONE 'utc')
)
) AS v(region_code, zagolovki, heshteg_local, text_post_maxsize_simbols, localities, created_at, updated_at)
WHERE NOT EXISTS (SELECT 1 FROM region_configs rc WHERE rc.region_code = v.region_code);

-- Соседи. Новым — только те коды, что существуют в `regions` (Зеленодольский, Лаишевский,
-- Чистопольский, Алексеевский, Казань и Марий Эл в сети не заведены). Уже живым — текущее
-- значение с прода (снято 2026-09-14) ПЛЮС новые коды, по алфавиту.
UPDATE regions SET neighbors = 'arsk,atnya,pestretsy' WHERE code = 'vysokaya_gora';
UPDATE regions SET neighbors = 'arsk,rybnaya_sloboda,tyulyachi,vysokaya_gora' WHERE code = 'pestretsy';
UPDATE regions SET neighbors = 'mamadysh,pestretsy,saby,tyulyachi' WHERE code = 'rybnaya_sloboda';
UPDATE regions SET neighbors = 'atnya,bal,pestretsy,saby,tyulyachi,vysokaya_gora' WHERE code = 'arsk';
UPDATE regions SET neighbors = 'arsk,vysokaya_gora' WHERE code = 'atnya';
UPDATE regions SET neighbors = 'arsk,mamadysh,pestretsy,rybnaya_sloboda,saby' WHERE code = 'tyulyachi';
UPDATE regions SET neighbors = 'arsk,bal,kukmor,mamadysh,rybnaya_sloboda,tyulyachi' WHERE code = 'saby';
UPDATE regions SET neighbors = 'kukmor,rybnaya_sloboda,saby,tyulyachi' WHERE code = 'mamadysh';

COMMIT;

-- Проверка после применения:
--   SELECT code, name, is_active, vk_group_id, neighbors FROM regions
--   WHERE code IN ('vysokaya_gora','pestretsy','rybnaya_sloboda','arsk','atnya','tyulyachi','saby','mamadysh')
--   ORDER BY code;
-- Симметричность (пустая выдача = все связи двусторонние):
--   SELECT a.code, b.code FROM regions a JOIN regions b
--     ON b.code = ANY(string_to_array(a.neighbors, ','))
--   WHERE NOT (a.code = ANY(string_to_array(coalesce(b.neighbors,''), ',')));
--
-- Rollback (возврат к состоянию после 102, снятому с прода 2026-09-14):
-- UPDATE regions SET neighbors = 'atnya,bal,saby,tyulyachi' WHERE code = 'arsk';
-- UPDATE regions SET neighbors = 'arsk' WHERE code = 'atnya';
-- UPDATE regions SET neighbors = 'arsk,mamadysh,saby' WHERE code = 'tyulyachi';
-- UPDATE regions SET neighbors = 'arsk,bal,kukmor,mamadysh,tyulyachi' WHERE code = 'saby';
-- UPDATE regions SET neighbors = 'kukmor,saby,tyulyachi' WHERE code = 'mamadysh';
-- DELETE FROM region_configs WHERE region_code IN ('vysokaya_gora','pestretsy','rybnaya_sloboda');
-- DELETE FROM regions WHERE code IN ('vysokaya_gora','pestretsy','rybnaya_sloboda');
