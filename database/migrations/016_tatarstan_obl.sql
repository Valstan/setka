-- 016: регион tatarstan_obl (oblast) + привязка районов Татарстана bal/kukmor.
--
-- Контекст. В миграции 015 введена иерархия strana → oblast → raion и создан
-- kirov_obl с привязкой 13 кировских районов. Два района — bal (БАЛТАСИ) и
-- kukmor (КУКМОР) — относятся к Татарстану, а не к Кировской области, поэтому
-- в 015 остались «сиротами» (parent_region_id IS NULL).
--
-- Эта миграция заводит областной регион Татарстана и привязывает к нему bal/kukmor:
--   1. Создать запись `tatarstan_obl` (kind=oblast, vk_group_id=-239149826, имя
--      «ТАТАРСТАН - ИНФО»). Это VK-группа https://vk.com/tatar_stan_info
--      (id=239149826 в groups.getById; аккаунт-владелец — админ, admin_level=3).
--   2. Прописать parent_region_id = (tatarstan_obl.id) у bal и kukmor.
--
-- После применения каскадный дайджест (modules/cascaded_digest.py, theme=oblast)
-- сможет собирать по 5 свежих постов с главных групп bal/kukmor и публиковать
-- в tatarstan_obl. Для публикации нужен community-токен группы tatar_stan_info
-- (добавить через /tokens как COMM_239149826, по аналогии с kirov_obl). До
-- появления токена дайджест собирается, но финальный wall.post падает с
-- «no publish-token available» (не VK-ошибка, авто-disable не взводит).
--
-- Beat-слоты `postopus-tatarstan-oblast-*` добавлены в tasks/celery_app.py.
--
-- Идемпотентна: повторное применение — no-op (ON CONFLICT / WHERE IS NULL).

-- Создание oblast-региона. ON CONFLICT (code) — если запись уже есть (повторное
-- применение / ручное создание), не перетираем имя/vk_group_id, только гарантируем kind.
INSERT INTO regions (code, name, vk_group_id, kind, is_active, created_at, updated_at)
VALUES ('tatarstan_obl', 'ТАТАРСТАН - ИНФО', -239149826, 'oblast', TRUE, NOW(), NOW())
ON CONFLICT (code) DO UPDATE SET kind = 'oblast';

-- Привязка районов Татарстана к tatarstan_obl. WHERE parent_region_id IS NULL —
-- чтобы не перетереть ручные изменения иерархии.
UPDATE regions
SET parent_region_id = (SELECT id FROM regions WHERE code = 'tatarstan_obl'),
    updated_at = NOW()
WHERE code IN ('bal', 'kukmor')
  AND parent_region_id IS NULL;
