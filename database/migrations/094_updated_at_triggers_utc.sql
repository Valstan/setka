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
-- ЗАЧЕМ ЭТО СИЛЬНЕЕ, ЧЕМ «неверное число». До правки одна и та же колонка
-- `updated_at` означала MSK в семи таблицах с триггером и UTC примерно в
-- пятнадцати без него (regions, communities, posts, filters, message_templates,
-- broadcast_campaigns, promo_settings, region_configs, work_tables,
-- oauth_clients, conveyor_deliveries и др., где пишет только ORM). Это была
-- межтабличная рассогласованность одного и того же поля, а не просто сдвиг.
--
-- Идемпотентна: CREATE OR REPLACE переустанавливает то же тело.
--
-- ⚠️ ТРИГГЕРЫ ДЕРЖАТ ФУНКЦИЮ ПО OID, А НЕ ПО ИМЕНИ (`pg_trigger.tgfoid`).
-- Пересоздавать их не нужно именно поэтому: CREATE OR REPLACE сохраняет OID и
-- подменяет только тело. Обратная сторона — ПЕРЕИМЕНОВАТЬ такую функцию нельзя:
-- это оборвёт все семь триггеров разом. (Прежняя редакция этой шапки
-- утверждала «ссылаются по имени» — вывод был верен, обоснование ложно, и оно
-- подсказывало будущему автору ровно неверный ход.)
--
-- РЕСТАРТ СЕРВИСОВ НЕ НУЖЕН. plpgsql кеширует скомпилированное тело в рамках
-- сессии, но привязывает кеш к версии кортежа `pg_proc` и перекомпилирует при
-- её смене — живые соединения пулов FastAPI и Celery подхватят новое тело сами.
--
-- ПРИЁМКА — детерминированная, не зависящая от трафика:
--   SELECT p.proname, pg_get_functiondef(p.oid)
--   FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
--   WHERE n.nspname='public'
--     AND p.proname IN ('update_updated_at_column','update_vk_tokens_updated_at');
--   -- обе должны содержать now() AT TIME ZONE 'utc'
--   SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal;  -- по-прежнему 7
-- Считать «строки с updated_at в будущем» для приёмки НЕЛЬЗЯ: такой запрос
-- видит только тронутые за последние три часа, и пустая выдача не доказывает
-- ничего.
--
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

-- ОТКАТ (вернёт московское время в updated_at семи таблиц). Расписан дословно
-- и целиком сознательно: аварийный откат делают ночью, и дописывать вторую
-- функцию по фразе «и то же для…» в такой момент — лишний шанс ошибиться.
--
--   CREATE OR REPLACE FUNCTION update_updated_at_column()
--   RETURNS TRIGGER AS $$
--   BEGIN
--       NEW.updated_at = CURRENT_TIMESTAMP;
--       RETURN NEW;
--   END;
--   $$ LANGUAGE plpgsql;
--
--   CREATE OR REPLACE FUNCTION update_vk_tokens_updated_at()
--   RETURNS TRIGGER AS $$
--   BEGIN
--       NEW.updated_at = CURRENT_TIMESTAMP;
--       RETURN NEW;
--   END;
--   $$ LANGUAGE plpgsql;
--
-- ПРИМЕЧАНИЕ. Файл правился ПОСЛЕ применения на проде (2026-09-01) — правки
-- только в комментариях, SQL не менялся. Журнал `applied_migrations` намеренно
-- не переписан: `applied_at` должен показывать, когда миграция реально
-- применилась, а не когда поправили её шапку.
