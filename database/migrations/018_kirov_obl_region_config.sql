-- 018: строка region_configs для kirov_obl (брендированные заголовки/хэштеги).
--
-- Контекст. С мая 2026 kirov_obl переведён с каскада на собственный пул
-- communities (regions.config->>'digest_mode' = 'communities', см. PR #88). Но
-- строку в region_configs ему не создавали — в отличие от всех 14 районов.
-- Из-за этого область молча выпадала из КАЖДОЙ тематической волны: гейт в
-- tasks/parsing_scheduler_tasks.run_all_regions_theme требовал
-- exists(RegionConfig.region_code == Region.code). Каскад при этом уже снят
-- (postopus-kirov-oblast-* удалены) → область не публиковала ничего с 30.05.
--
-- Код-фикс гейта (этот же PR) пускает community-mode регионы в волну и без
-- region_configs (parse_and_publish_theme подставляет safe-defaults, заголовки
-- имеют fallback по теме+имени региона). Но fallback берёт сырое имя региона
-- «КИРОВСКАЯ ОБЛАСТЬ - ИНФО» → заголовок выходит уродливый
-- («Наука и образование КИРОВСКАЯ ОБЛАСТЬ - ИНФО:»). Эта миграция задаёт
-- человекочитаемые брендированные заголовки/хэштеги по 12 областным темам.
--
-- См. modules/publisher/postopus_digest_headers.py:
--   resolve_digest_header  — zagolovki[theme] имеет приоритет над fallback;
--   resolve_digest_hashtags — heshteg[theme] (тематический тег) + heshteg_local
--                             .raicentr (#Киров43 локальным тегом).
--
-- Идемпотентна: ON CONFLICT (region_code) DO NOTHING — не клобберит ручную
-- настройку через UI /regions, если строка уже появилась.

INSERT INTO region_configs (
    region_code, zagolovki, heshteg, heshteg_local,
    text_post_maxsize_simbols, setka_regim_repost, created_at, updated_at
)
VALUES (
    'kirov_obl',
    '{
        "novost": "Новости Кировской области:",
        "proisshestviya": "Происшествия в Кировской области:",
        "molodezh": "Молодёжь Кировской области:",
        "nauka": "Наука и образование Кировской области:",
        "promyshlennost": "Экономика и промышленность Кировской области:",
        "selhoz": "Сельское хозяйство Кировской области:",
        "zdorovie": "Здоровье и медицина Кировской области:",
        "zhkh": "ЖКХ Кировской области:",
        "priroda": "Природа и туризм Кировской области:",
        "kultura": "Культура Кировской области:",
        "sport": "Спорт Кировской области:",
        "admin": "Власть и общество Кировской области:"
    }'::json,
    '{
        "novost": "новостиКиров43",
        "proisshestviya": "происшествияКиров43",
        "molodezh": "молодёжьКиров43",
        "nauka": "наукаКиров43",
        "promyshlennost": "экономикаКиров43",
        "selhoz": "сельхозКиров43",
        "zdorovie": "здоровьеКиров43",
        "zhkh": "жкхКиров43",
        "priroda": "природаКиров43",
        "kultura": "культураКиров43",
        "sport": "спортКиров43",
        "admin": "властьКиров43"
    }'::json,
    '{"raicentr": "Киров43"}'::json,
    4096,
    FALSE,
    NOW(),
    NOW()
)
ON CONFLICT (region_code) DO NOTHING;
