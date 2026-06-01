-- 019: tatarstan_obl → community-mode + строка region_configs (брендинг).
--
-- Контекст. tatarstan_obl (kind=oblast, vk_group_id=-239149826,
-- vk.com/tatar_stan_info) заведён миграцией 016 на каскаде (дайджест-дайджестов
-- из районов bal/kukmor). По образцу kirov_obl (PR #88/#95, миграция 018)
-- переводим его на СОБСТВЕННЫЙ пул communities: область сама собирает
-- тематические дайджесты из своих сообществ, а не каскадом из районов.
--
-- Что делает миграция:
--   1. regions.config += {"digest_mode": "communities"} (merge, geo сохраняется).
--      После этого _should_cascade(...) возвращает False, и tasks.
--      parsing_scheduler_tasks.run_all_regions_theme допускает область в
--      тематические волны (гейт config_gate уже поддерживает community-mode,
--      см. PR #95) — без правок кода/beat (волны региона-агностичны).
--   2. INSERT строки region_configs с человекочитаемыми брендированными
--      заголовками/хэштегами по 12 темам — иначе fallback берёт сырое имя
--      региона и заголовок выходит уродливый. Локальный тег #Татарстан16
--      (миррор #Киров43: имя региона + автокод). См.
--      modules/publisher/postopus_digest_headers.py (resolve_digest_header /
--      resolve_digest_hashtags).
--
-- ВАЖНО: пул tatarstan_obl на момент миграции ПУСТ. Гейт волны требует
-- has_any_communities, поэтому до засева пула (через /discover_communities)
-- область в волну не попадёт и публиковать не будет — это безопасно (no-op),
-- но публикация начнётся только после наполнения пула.
--
-- Дети bal/kukmor (raion) продолжают публиковать свои районные дайджесты
-- независимо — community-mode отключает только КАСКАД родителя, не детей.
--
-- Публикация области идёт через community-токен COMM_239149826 (валиден в БД).
--
-- Идемпотентна: UPDATE guard'ится IS DISTINCT FROM, INSERT — ON CONFLICT
-- (region_code) DO NOTHING (не клобберит ручную настройку через UI /regions).

-- 1. Перевод в community-mode (merge в json через jsonb, geo сохраняется).
UPDATE regions
SET config = (config::jsonb || '{"digest_mode": "communities"}'::jsonb)::json,
    updated_at = NOW()
WHERE code = 'tatarstan_obl'
  AND (config ->> 'digest_mode') IS DISTINCT FROM 'communities';

-- 2. Брендированные заголовки/хэштеги (12 областных тем).
INSERT INTO region_configs (
    region_code, zagolovki, heshteg, heshteg_local,
    text_post_maxsize_simbols, setka_regim_repost, created_at, updated_at
)
VALUES (
    'tatarstan_obl',
    '{
        "novost": "Новости Татарстана:",
        "proisshestviya": "Происшествия в Татарстане:",
        "molodezh": "Молодёжь Татарстана:",
        "nauka": "Наука и образование Татарстана:",
        "promyshlennost": "Экономика и промышленность Татарстана:",
        "selhoz": "Сельское хозяйство Татарстана:",
        "zdorovie": "Здоровье и медицина Татарстана:",
        "zhkh": "ЖКХ Татарстана:",
        "priroda": "Природа и туризм Татарстана:",
        "kultura": "Культура Татарстана:",
        "sport": "Спорт Татарстана:",
        "admin": "Власть и общество Татарстана:"
    }'::json,
    '{
        "novost": "новостиТатарстан16",
        "proisshestviya": "происшествияТатарстан16",
        "molodezh": "молодёжьТатарстан16",
        "nauka": "наукаТатарстан16",
        "promyshlennost": "экономикаТатарстан16",
        "selhoz": "сельхозТатарстан16",
        "zdorovie": "здоровьеТатарстан16",
        "zhkh": "жкхТатарстан16",
        "priroda": "природаТатарстан16",
        "kultura": "культураТатарстан16",
        "sport": "спортТатарстан16",
        "admin": "властьТатарстан16"
    }'::json,
    '{"raicentr": "Татарстан16"}'::json,
    4096,
    FALSE,
    NOW(),
    NOW()
)
ON CONFLICT (region_code) DO NOTHING;
