-- 088: durable-пауза модуля «Раскрутка» (этап 1, 2026-08-28).
--
-- ЗАЧЕМ. Коды VK 9 (flood control) и 14 (captcha) означают «ВК считает нас спамером»
-- и обязаны останавливать весь модуль, а не одно действие. Естественное место для
-- такого флага — Redis, но квоты на Redis у нас сознательно fail-open (см.
-- modules/gateway/quota.py): при недоступном Redis запрос проходит. Для суточной
-- квоты это приемлемо, для «ВК велел замолчать» — нет: перезапуск Redis снял бы
-- паузу, и модуль продолжил бы публиковать ровно в тот момент, когда этого делать
-- нельзя категорически.
--
-- Поэтому пауза живёт в БД, а Redis остаётся быстрым отказом поверх неё.
--
-- paused_until NULL = модуль не на паузе. Непустое значение (UTC) ставит
-- обработчик ошибок; снимается временем либо владельцем из UI.
-- paused_reason хранит код и человеческую расшифровку — без неё владелец видит
-- «модуль молчит» и не знает, это авария или так задумано.
--
-- Идемпотентна (ADD COLUMN IF NOT EXISTS). Применять с ON_ERROR_STOP=1.

ALTER TABLE promo_settings ADD COLUMN IF NOT EXISTS paused_until TIMESTAMP;
ALTER TABLE promo_settings ADD COLUMN IF NOT EXISTS paused_reason TEXT;

-- Откат:
-- ALTER TABLE promo_settings DROP COLUMN IF EXISTS paused_until,
--     DROP COLUMN IF EXISTS paused_reason;
