-- 015: иерархия регионов (strana → oblast → raion) + запись kirov_obl.
--
-- Контекст. До этой миграции `regions` была плоская таблица: 15 районных групп
-- (mi, vp, ur, nolinsk, …). Облáстной дайджест «КИРОВСКАЯ ОБЛАСТЬ - ИНФО»
-- собирался специальным модулем `modules/kirov_oblast_digest.py`, который читал
-- стены районных групп, извлекал из текста дайджестов ссылки `vk.com/wall*_*` и
-- по ним загружал исходные посты. Эта механика хрупкая (зависит от того, что
-- район публикует именно «дайджест со ссылками») и переставала работать —
-- 2026-05 на проде `total_groups_checked=0`, дайджест мёртв. Плюс самой записи
-- `kirov_obl` в `regions` нет, поэтому beat-таски `postopus-kirov-oblast-*`
-- упирались в «Region kirov_obl missing».
--
-- Решение — ввести иерархию регионов из трёх типов:
--   * raion  — район (низший уровень). Источники = сообщества-партнёры VK
--              (текущая таблица `communities`, как было). Публикует свой
--              дайджест в `region.vk_group_id`.
--   * oblast — область. Источники = главные сообщества подчинённых районов
--              (поле `parent_region_id`). По 5 свежих постов с каждого ребёнка.
--   * strana — страна. Источники = главные сообщества подчинённых областей.
--
-- Универсальная логика для oblast и strana — в `modules/cascaded_digest.py`.
--
-- Добавляемые поля:
--   * regions.kind VARCHAR(20) NOT NULL DEFAULT 'raion' — тип региона.
--   * regions.parent_region_id INTEGER NULL REFERENCES regions(id) ON DELETE SET NULL —
--     ссылка на родителя в иерархии. Для strana и для legacy-районов = NULL.
--
-- Заполнение:
--   1. Создать запись `kirov_obl` (kind=oblast, vk_group_id=-168170001, имя
--      «КИРОВСКАЯ ОБЛАСТЬ - ИНФО»). Это VK-группа
--      https://vk.com/kirovskaya_info (id=168170001 в groups.getById).
--   2. Прописать parent_region_id = (kirov_obl.id) у 13 кировских районов:
--      arbazh, klz, leb, mi, nema, nolinsk, pizhanka, sovetsk, tuzha, ur,
--      verhoshizhem, vp. (bal и kukmor — Татарстан, не Кировская область,
--      родителя пока нет — добавим отдельной миграцией если появится tatarstan_obl).
--
-- Идемпотентна: повторное применение — no-op (IF NOT EXISTS / ON CONFLICT DO NOTHING).

ALTER TABLE regions
    ADD COLUMN IF NOT EXISTS kind VARCHAR(20) NOT NULL DEFAULT 'raion',
    ADD COLUMN IF NOT EXISTS parent_region_id INTEGER NULL REFERENCES regions(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_regions_kind ON regions(kind);
CREATE INDEX IF NOT EXISTS idx_regions_parent ON regions(parent_region_id);

-- Создание oblast-региона. ON CONFLICT (code) — если запись уже есть (повторное
-- применение миграции), оставляем как было — пользователь мог поменять имя или
-- vk_group_id вручную.
INSERT INTO regions (code, name, vk_group_id, kind, is_active, created_at, updated_at)
VALUES ('kirov_obl', 'КИРОВСКАЯ ОБЛАСТЬ - ИНФО', -168170001, 'oblast', TRUE, NOW(), NOW())
ON CONFLICT (code) DO UPDATE SET kind = 'oblast';

-- Привязка районов Кировской области к kirov_obl. WHERE parent_region_id IS NULL —
-- чтобы не перетереть ручные изменения, если кто-то уже двигал иерархию.
UPDATE regions
SET parent_region_id = (SELECT id FROM regions WHERE code = 'kirov_obl'),
    updated_at = NOW()
WHERE code IN ('arbazh', 'klz', 'leb', 'mi', 'nema', 'nolinsk',
               'pizhanka', 'sovetsk', 'tuzha', 'ur', 'verhoshizhem', 'vp')
  AND parent_region_id IS NULL;
