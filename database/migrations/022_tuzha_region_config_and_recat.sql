-- 022: Тужа (raion) — строка region_configs (брендинг) + перекатегоризация пула.
--
-- Контекст. Тужа (kind=raion, id=19, vk_group_id=-239050321, parent=kirov_obl)
-- заведена через визард /regions/new. Визард создаёт только запись в `regions`,
-- но НЕ строку `region_configs` (её исторически создавала лишь миграция из Mongo).
-- Гейт тематических волн (tasks.parsing_scheduler_tasks.run_all_regions_theme)
-- до PR этой нитки пускал регион только при наличии строки region_configs ИЛИ
-- config.digest_mode='communities'. У Тужи нет ни того, ни другого → она молча
-- выпадала из ВСЕХ волн, хотя пул из 49 communities есть (0 публикаций).
--
-- Корневой фикс — в коде (config_gate теперь пускает регион с активным пулом
-- communities), он чинит и онбординг будущих районов. Эта миграция —
-- сопутствующая: даёт Туже человекочитаемые брендированные заголовки/хэштеги
-- (иначе fallback берёт сырое имя «Тужа, Кировская область» → уродливый
-- заголовок и хэштег «#новости» без привязки) и приводит в порядок категории
-- пула. См. modules/publisher/postopus_digest_headers.py.
--
-- Что делает миграция:
--   1. INSERT строки region_configs для 'tuzha':
--      - zagolovki по районным темам (novost/reklama/kultura/sport/admin/union/
--        detsad/addons);
--      - heshteg_local.raicentr='Тужа' → fallback хэштегов даёт «#новостиТужа …
--        #Тужа» (heshteg оставляем NULL — fallback по теме+raicentr);
--      - localities копируем из regions.config (54 нп) — для RegionalRelevanceFilter.
--   2. Перекатегоризация пула (UPDATE по PK id, guard region_id=19):
--      detsad был свалкой школ/движений. Канон-таксономия района:
--        detsad = только детские сады; union = школы/Движение Первых/РДШ/ЮИД/ДДТ;
--        sport = спортшкола; admin = гос. соц. учреждение; sosed (происшествия) —
--        сельский чат туда не относится → novost.
--
-- Идемпотентна: INSERT — ON CONFLICT (region_code) DO NOTHING (не клобберит
-- ручную настройку через UI); UPDATE'ы guard'ятся текущей категорией.

-- 1. Строка region_configs (localities тянем из regions.config — без ручного
--    перебора 54 названий и в синхроне с источником).
INSERT INTO region_configs (
    region_code, zagolovki, heshteg_local, localities,
    text_post_maxsize_simbols, setka_regim_repost, created_at, updated_at
)
SELECT
    'tuzha',
    '{
        "novost": "Новости Тужинского района:",
        "reklama": "Объявления Тужинского района:",
        "kultura": "Культура Тужинского района:",
        "sport": "Спорт Тужинского района:",
        "admin": "Власть и общество Тужинского района:",
        "union": "Молодёжь и образование Тужи:",
        "detsad": "Детские сады Тужи:",
        "addons": "Тужинский район — также:"
    }'::json,
    '{"raicentr": "Тужа"}'::json,
    (r.config::jsonb -> 'localities')::json,
    4096,
    FALSE,
    NOW(),
    NOW()
FROM regions r
WHERE r.code = 'tuzha'
ON CONFLICT (region_code) DO NOTHING;

-- 2a. detsad → union (школы, РДШ, ЮИД, «Первые»/Движение Первых, ДДТ).
UPDATE communities
SET category = 'union', updated_at = NOW()
WHERE region_id = 19
  AND category = 'detsad'
  AND id IN (774, 766, 726, 767, 772, 776, 775, 773);

-- 2b. detsad → sport (МБУ ДО СШ пгт Тужа — спортивная школа).
UPDATE communities
SET category = 'sport', updated_at = NOW()
WHERE region_id = 19 AND category = 'detsad' AND id = 768;

-- 2c. detsad → admin (КОГБУ «Центр помощи детям» — гос. соц. учреждение).
UPDATE communities
SET category = 'admin', updated_at = NOW()
WHERE region_id = 19 AND category = 'detsad' AND id = 770;

-- 2d. sosed → novost (сельский чат «Тужа-Коврижата-Караванное», не происшествия;
--     после этого у Тужи 0 sosed-источников — sosed-волна её корректно минует).
UPDATE communities
SET category = 'novost', updated_at = NOW()
WHERE region_id = 19 AND category = 'sosed' AND id = 729;
