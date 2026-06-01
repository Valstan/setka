-- 020: восстановление репостов в Telegram (Малмыж + Гоньба).
--
-- Контекст. Владелец просит восстановить два потока репостов в Telegram,
-- работавших в старом Postopus, но не портированных в SARAFAN:
--   A. Дайджесты района «mi» (Малмыж-Инфо, VK -158787639) → канал @malmyzh_info
--      ботом AFONYA (@malm_info_bot).
--   B. Стена ВК-сообщества «Гоньба - жемчужина Вятки» (vk_id -218688001) →
--      канал @gonba_life ботом VALSTANBOT (@valstan_bot).
-- Боты живы, их токены уже в /etc/setka/setka.env (TELEGRAM_TOKEN_AFONYA /
-- TELEGRAM_TOKEN_VALSTANBOT), оба — админы каналов. Секреты остаются ТОЛЬКО в
-- env (pool #008): в БД хранится лишь канал + ИМЯ бота, не токен.
--
-- Что делает миграция:
--   1. Чинит устаревший канал района mi: '@malmig_info' → '@malmyzh_info'
--      (бот не видел старый username, getChat → chat not found).
--   2. regions.config += {"telegram_bot": "AFONYA"} (merge через jsonb, geo/
--      digest_mode сохраняются). Flow-A хук в parse_and_publish_theme зеркалит
--      дайджест только если у региона задан telegram_channel И config.telegram_bot.
--   3. communities += telegram_channel/telegram_bot (для Flow B; PR2 их читает,
--      но колонки безвредны до использования).
--   4. Сидирует сообщество «Гоньба» (id 847, vk_id -218688001) каналом
--      @gonba_life и ботом VALSTANBOT.
--
-- Идемпотентна: UPDATE guard'ятся IS DISTINCT FROM, ALTER — IF NOT EXISTS.

-- 1. Починка устаревшего TG-канала района mi.
UPDATE regions
SET telegram_channel = '@malmyzh_info',
    updated_at = NOW()
WHERE code = 'mi'
  AND telegram_channel IS DISTINCT FROM '@malmyzh_info';

-- 2. Имя бота-постера для Flow A (merge в json через jsonb).
UPDATE regions
SET config = (COALESCE(config, '{}'::json)::jsonb || '{"telegram_bot": "AFONYA"}'::jsonb)::json,
    updated_at = NOW()
WHERE code = 'mi'
  AND (config ->> 'telegram_bot') IS DISTINCT FROM 'AFONYA';

-- 3. Колонки TG-таргета для сообществ (Flow B).
ALTER TABLE communities ADD COLUMN IF NOT EXISTS telegram_channel varchar(100);
ALTER TABLE communities ADD COLUMN IF NOT EXISTS telegram_bot varchar(50);

-- 4. Сид «Гоньба - жемчужина Вятки» → @gonba_life ботом VALSTANBOT.
UPDATE communities
SET telegram_channel = '@gonba_life',
    telegram_bot = 'VALSTANBOT',
    updated_at = NOW()
WHERE id = 847
  AND vk_id = -218688001
  AND (telegram_channel IS DISTINCT FROM '@gonba_life'
       OR telegram_bot IS DISTINCT FROM 'VALSTANBOT');
