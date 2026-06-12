-- 037: пользователи контент-радара + auth с изоляцией ролей (Ф0.1, директива
-- brain 2026-06-11 «content-radar kickoff», план — mailbox/to-brain/
-- 2026-06-12-content-radar-f0-plan.md).
--
-- Контекст. setka исторически была headless (доступ через SSH-туннель), auth в
-- приложении не было вовсе. Probe Ф0 (PR #196) показал, что операторский UI
-- торчит в интернет через HTTPS-техдомен — временно закрыт nginx basic-auth.
-- Эта таблица — фундамент app-level auth: операторы видят весь setka,
-- radar-юзеры — только свой контент-радар (источники/лента/архив).
--
-- Квоты архива (quota_bytes/used_bytes) закладываются в схему сразу (решение
-- владельца: «вечно + предупредительные квоты»), enforcement — Ф1.
--
-- Идемпотентна: CREATE TABLE IF NOT EXISTS, GRANT повторно no-op.

CREATE TABLE IF NOT EXISTS radar_users (
    id              BIGSERIAL PRIMARY KEY,
    login           VARCHAR(64)  NOT NULL UNIQUE,
    password_hash   VARCHAR(256) NOT NULL,            -- scrypt$<n>$<r>$<p>$<salt>$<hash>
    role            VARCHAR(16)  NOT NULL DEFAULT 'radar',  -- operator|radar
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,

    -- Архив (механика квот — Ф1, схема — сразу):
    quota_bytes     BIGINT       NOT NULL DEFAULT 209715200,  -- 200 MB дефолт
    used_bytes      BIGINT       NOT NULL DEFAULT 0,

    created_at      TIMESTAMP    NOT NULL DEFAULT NOW(),
    last_login_at   TIMESTAMP
);

GRANT ALL PRIVILEGES ON TABLE radar_users TO setka_user;
GRANT USAGE, SELECT ON SEQUENCE radar_users_id_seq TO setka_user;
