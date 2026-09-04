-- 095: планировщик предложки — оригинал с подписью автора + репосты в соседей
-- (Этап 0 программы «Кабинет под ключ», план владельца 2026-09-05).
--
-- ЗАЧЕМ. Владелец хочет из /ad брать предложенные посты (ad_requests
-- origin='suggested'), ставить каждому дату выхода и дублировать в соседние
-- сообщества. Решение владельца: в исходном сообществе публикуется САМ
-- предложенный пост с подписью автора (wall.post post_id=<suggest> signed=1,
-- только user-токеном админа), а в дублёры уходит wall.repost этой записи от
-- имени сообщества-дублёра ПОСЛЕ фактического выхода оригинала (у wall.repost
-- нет publish_date, репостить можно только вышедшее).
--
-- ПОЧЕМУ НЕ НОВАЯ ТАБЛИЦА. Каждое размещение (оригинал и каждый дублёр) —
-- обычная строка ad_scheduled_posts с client_id/price: тогда счётчики
-- «заказано/вышло/оплачено», реконсилер (AdPublication + AdPayment awaiting),
-- отмена и возврат в пакет работают без второго журнала (урок AdOrderItem —
-- modules/ad_cabinet/balance.py:8-9).
--
--   kind            'post' (всё, что было) | 'suggested' (оригинал предложки) |
--                   'repost' (дублёр; vk_postponed_post_id пуст до выхода —
--                   реконсилер такие строки не берёт по построению);
--   source_post_id  для 'repost' — строка-оригинал (CASCADE: без оригинала
--                   репост бессмыслен);
--   next_attempt_at МСК wall-clock naive (та же шкала, что publish_date) —
--                   когда диспетчер вправе взять строку; сдвигается вперёд как
--                   lease при claim (guarded UPDATE, образец packages.consume);
--   attempts        счётчик попыток репоста (предел ретраев + UI).
--
-- Уникумы держат идемпотентность в БД, а не только в коде (урок аудита
-- 2026-09-05: «1 пост в день» и «нет дублей» держались только чтением):
--   один репост в одно сообщество на один оригинал;
--   одна активная публикация оригинала на заявку (failed/cancelled можно
--   переиграть).
--
-- Идемпотентна (IF NOT EXISTS). Без now()-дефолтов (сторож
-- tests/test_migrations_utc_convention.py). Без BEGIN/COMMIT — их даёт раннер.

ALTER TABLE ad_scheduled_posts
    ADD COLUMN IF NOT EXISTS kind VARCHAR(20) NOT NULL DEFAULT 'post';

ALTER TABLE ad_scheduled_posts
    ADD COLUMN IF NOT EXISTS source_post_id BIGINT
        REFERENCES ad_scheduled_posts(id) ON DELETE CASCADE;

ALTER TABLE ad_scheduled_posts
    ADD COLUMN IF NOT EXISTS next_attempt_at TIMESTAMP;

ALTER TABLE ad_scheduled_posts
    ADD COLUMN IF NOT EXISTS attempts SMALLINT NOT NULL DEFAULT 0;

CREATE UNIQUE INDEX IF NOT EXISTS uq_ad_sched_repost_target
    ON ad_scheduled_posts(source_post_id, community_vk_id)
    WHERE source_post_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_ad_sched_suggested_active
    ON ad_scheduled_posts(source_ad_request_id)
    WHERE kind = 'suggested' AND status IN ('scheduled', 'published');

CREATE INDEX IF NOT EXISTS ix_ad_sched_due
    ON ad_scheduled_posts(kind, status, next_attempt_at);
