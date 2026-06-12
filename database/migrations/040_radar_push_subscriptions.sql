-- 040: контент-радар Ф0.5 — web-push подписки (план —
-- mailbox/to-brain/2026-06-12-content-radar-f0-plan.md, последний срез Ф0).
--
-- Браузерная push-подписка юзера (PushSubscription из Push API): endpoint —
-- уникальный URL push-сервиса браузера, p256dh/auth — ключи шифрования
-- payload'а. У юзера может быть несколько подписок (телефон + десктоп).
-- Ошибки 404/410 от push-сервиса = подписка умерла → строка удаляется
-- автоматически (modules/radar/push.py).
--
-- VAPID-ключи — в /etc/setka/setka.env (#008): RADAR_VAPID_PRIVATE_KEY
-- (base64url raw EC P-256) + RADAR_VAPID_SUBJECT (mailto:).
--
-- Идемпотентна: CREATE TABLE/INDEX IF NOT EXISTS, GRANT повторно no-op.

CREATE TABLE IF NOT EXISTS radar_push_subscriptions (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT NOT NULL REFERENCES radar_users(id) ON DELETE CASCADE,
    endpoint        VARCHAR(1024) NOT NULL UNIQUE,
    p256dh          VARCHAR(256)  NOT NULL,
    auth            VARCHAR(128)  NOT NULL,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    last_success_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_radar_push_subscriptions_user_id
    ON radar_push_subscriptions (user_id);

GRANT ALL PRIVILEGES ON TABLE radar_push_subscriptions TO setka_user;
GRANT USAGE, SELECT ON SEQUENCE radar_push_subscriptions_id_seq TO setka_user;
