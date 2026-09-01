-- 094: триггеры updated_at пишут UTC, а не московское время (2026-09-01).
--
-- ЗАЧЕМ. На проде `SHOW timezone` = Host, поэтому `CURRENT_TIMESTAMP` в SQL
-- отдаёт МОСКОВСКОЕ время, а приложение пишет наивный UTC (модели стоят на
-- `datetime.utcnow`). Разница ровно три часа. Две BEFORE UPDATE-функции ставят
-- `NEW.updated_at = CURRENT_TIMESTAMP` и тем самым МОЛЧА ЗАТИРАЮТ значение,
-- присланное приложением, — на каждом апдейте семи таблиц.
--
-- ЭТО НЕ СПЯЩИЙ ДЕФЕКТ, в отличие от `DEFAULT now()` в тех же миграциях.
-- Дефолты действительно не срабатывают: сырых `INSERT` в проекте нет, ORM
-- всегда подставляет значение сам. Триггеры срабатывают всегда. Запись в
-- PENDING называла дефект спящим целиком — это было верно наполовину.
--
-- ЗАМЕР НА ПРОДЕ 2026-09-01 (перекос доказан, а не выведен рассуждением):
--   SHOW timezone                      → Host
--   now() - (now() AT TIME ZONE 'utc') → 03:00:00
--   ad_clients id=12: created_at 2026-08-30 22:12:40 → updated_at 01:12:50,
--     diff 03:00:09 — строка обновлена через девять секунд после создания,
--     а метка ушла на три часа вперёд;
--   ad_clients id=11 → diff 03:01:11; id=7 → diff 03:00:06;
--   vk_tokens id=4: updated_at 2026-09-01 08:00:17 при UTC-времени 05:04:17 —
--     значение на три часа В БУДУЩЕМ прямо сейчас.
--
-- ОХВАТ. Запрос к pg_trigger на живой базе (а не грепом по миграциям) дал ровно
-- семь триггеров и ровно эти две функции — третьей нет:
--   update_updated_at_column     → ad_clients, ad_order_items, ad_publications,
--                                  ad_requests, ad_scheduled_posts,
--                                  community_candidates
--   update_vk_tokens_updated_at  → vk_tokens
-- Обе функции в базе байт-в-байт совпадают с текстом из миграций 003/011/021/
-- 025/027, то есть дублирующие определения не разошлись.
--
-- ЦЕНА СЕГОДНЯ И ПОСЛЕ ПРАВКИ. Арифметики по `updated_at` в коде нет: окна
-- считаются по `created_at`/`detected_at`/`paid_at`/`published_at`, а их пишет
-- ORM в UTC. Единственное место, где значение видно человеку, —
-- `web/api/ad_crm.py:291`, сортировка списка клиентов `updated_at DESC`. После
-- правки свежие значения станут на три часа МЕНЬШЕ старых, поэтому в течение
-- трёх часов после выката недавно тронутый клиент может встать в списке ниже
-- тронутого раньше. Эффект самозаживающий: как только все живые строки
-- получат новую метку, порядок снова верен. Данные НЕ переписываем сознательно —
-- переписывание молча испортило бы историю, а цена вопроса тут ниже риска.
--
-- Идемпотентна: CREATE OR REPLACE переустанавливает то же тело; сами триггеры
-- не трогаем — они ссылаются на функцию по имени и подхватят новую версию.
-- Без собственных BEGIN/COMMIT — их даёт scripts/migrate.py (см. шапку 090).

CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = (now() AT TIME ZONE 'utc');
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION update_vk_tokens_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = (now() AT TIME ZONE 'utc');
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Откат (вернёт московское время в updated_at семи таблиц):
--   CREATE OR REPLACE FUNCTION update_updated_at_column()
--   RETURNS TRIGGER AS $$
--   BEGIN
--       NEW.updated_at = CURRENT_TIMESTAMP;
--       RETURN NEW;
--   END;
--   $$ LANGUAGE plpgsql;
--   (и то же для update_vk_tokens_updated_at)
