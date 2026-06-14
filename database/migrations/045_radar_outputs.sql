-- 045: личный кабинет радара — целевые каналы вывода + пауза подписок
-- (директива brain 2026-06-14 radar-personal-cabinet).
--
-- Дельта поверх Ф0/Ф1 (лента/архив/источники/web-push уже есть):
--   1) radar_subscriptions.is_active — per-user пауза источника без удаления
--      (fan-out не страдает: источник поллится, пока на него есть ХОТЬ ОДНА
--      подписка; пауза лишь убирает его из ленты/выводов этого юзера);
--   2) radar_outputs — куда радар шлёт найденное (ядро запроса владельца):
--      feed (внутренняя лента, дефолт) | telegram (бот sendMessage) |
--      vk (wall.post). Каждый вывод — отдельная запись (тип + цель + режим +
--      вкл/выкл), пользователь сам набирает свой набор.
--
-- Probe-факт 2026-06-14 (этот бокс myjino): api.telegram.org ДОСТУПЕН (302/0.2с,
-- intake-бот getUpdates тикает) — G63 здесь не материализуется, TG-вывод текстом
-- идёт напрямую, relay для Bot API не нужен (relay только для чтения t.me/s/ и
-- CDN-медиа). VK wall.post установлен probe-ами рассылки/обложек (16/16).
--
-- Режим пересылки (mode): excerpt_link (начало+ссылка, дефолт — дёшево, не
-- упирается в медиа/лимиты) | full (целиком). Per-вывод.
--
-- Курсор доставки (last_item_id): at-most-once по монотонному radar_items.id.
-- При создании инициализируется текущим MAX(id), чтобы новый вывод не выстрелил
-- бэклогом — шлются только элементы, пришедшие ПОСЛЕ подключения вывода.
--
-- Идемпотентна: ADD COLUMN / CREATE TABLE IF NOT EXISTS, GRANT повторно no-op.

ALTER TABLE radar_subscriptions
    ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;

CREATE TABLE IF NOT EXISTS radar_outputs (
    id               BIGSERIAL PRIMARY KEY,
    user_id          BIGINT NOT NULL REFERENCES radar_users (id) ON DELETE CASCADE,
    type             VARCHAR(16) NOT NULL,                  -- feed|telegram|vk
    title            VARCHAR(200),                          -- человекочитаемая метка
    target           VARCHAR(512),                          -- tg: chat_id/@channel; vk: owner_id; feed: NULL
    mode             VARCHAR(16) NOT NULL DEFAULT 'excerpt_link',  -- excerpt_link|full
    config           JSON,                                  -- {bot_name?} и пр. (креды-ref в env, не тут)
    is_active        BOOLEAN NOT NULL DEFAULT TRUE,
    last_item_id     BIGINT NOT NULL DEFAULT 0,             -- курсор доставки (at-most-once по item.id)
    last_delivery_at TIMESTAMP,
    fail_count       INTEGER NOT NULL DEFAULT 0,
    last_error       VARCHAR(512),
    created_at       TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc')
);
CREATE INDEX IF NOT EXISTS ix_radar_outputs_user ON radar_outputs (user_id);
CREATE INDEX IF NOT EXISTS ix_radar_outputs_active ON radar_outputs (is_active);

-- Права рантайм-роли приложения (как в предыдущих миграциях радара).
GRANT ALL PRIVILEGES ON TABLE radar_outputs TO setka_user;
GRANT USAGE, SELECT ON SEQUENCE radar_outputs_id_seq TO setka_user;
