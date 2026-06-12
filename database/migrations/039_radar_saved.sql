-- 039: контент-радар Ф0.4 — save-архив + курсор новизны ленты (план —
-- mailbox/to-brain/2026-06-12-content-radar-f0-plan.md).
--
-- radar_saved — личный архив юзера. СНИМОК контента (text/title/url/media),
-- а не FK-ссылка на содержимое radar_items: элементы ленты подлежат ретенции
-- (~30 дней), сохранёнки живут вечно (решение владельца). item_id остаётся
-- для дедупа «уже сохранено» и гаснет в NULL при чистке элемента.
--
-- media — JSON-список [{type, url|file, bytes}]: фото скачиваются на диск
-- (RADAR_ARCHIVE_DIR, дефолт /var/lib/setka/radar_archive/<user_id>/<saved_id>/),
-- видео — ссылкой (решение владельца). used_bytes на radar_users уже есть (037).
--
-- last_seen_item_id на radar_users — курсор новизны: всё с id больше курсора
-- UI показывает как непрочитанное.
--
-- Идемпотентна: IF NOT EXISTS везде, GRANT повторно no-op.

CREATE TABLE IF NOT EXISTS radar_saved (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT NOT NULL REFERENCES radar_users(id) ON DELETE CASCADE,
    item_id         BIGINT REFERENCES radar_items(id) ON DELETE SET NULL,

    -- Снимок контента на момент сохранения:
    source_title    VARCHAR(256),
    url             VARCHAR(1024),
    title           VARCHAR(512),
    text            TEXT,
    media           JSON,                              -- [{type, url|file, bytes}]
    published_at    TIMESTAMP,

    archived_bytes  BIGINT NOT NULL DEFAULT 0,         -- сколько байт легло на диск
    saved_at        TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Один и тот же элемент ленты не сохраняется дважды (пока item жив).
CREATE UNIQUE INDEX IF NOT EXISTS uq_radar_saved_user_item
    ON radar_saved (user_id, item_id) WHERE item_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_radar_saved_user_saved_at
    ON radar_saved (user_id, saved_at DESC);

ALTER TABLE radar_users ADD COLUMN IF NOT EXISTS last_seen_item_id BIGINT;

GRANT ALL PRIVILEGES ON TABLE radar_saved TO setka_user;
GRANT USAGE, SELECT ON SEQUENCE radar_saved_id_seq TO setka_user;
