-- 076: расширение ad_publications — комментарии, статус оплаты, уникальность поста
-- + кэш имени сообщества для группировки в UI без лишних VK-запросов.
-- Применять с ON_ERROR_STOP=1 (гейт #025).

BEGIN;

ALTER TABLE ad_publications
    ADD COLUMN IF NOT EXISTS comments INTEGER DEFAULT 0;

ALTER TABLE ad_publications
    ADD COLUMN IF NOT EXISTS paid_status VARCHAR(20) DEFAULT 'unpaid';

ALTER TABLE ad_publications
    ADD COLUMN IF NOT EXISTS community_name VARCHAR(300);

ALTER TABLE ad_publications
    ADD COLUMN IF NOT EXISTS community_screen_name VARCHAR(200);

-- Значения по умолчанию для существующих строк (published_at было now(), ставим paid_status).
-- published_at у существующих строк заполнено (NOT NULL с 027), paid_status — дефолт.
UPDATE ad_publications SET paid_status = 'unpaid' WHERE paid_status IS NULL;

-- Дедупликация: один и тот же пост не может быть привязан дважды к одному сообществу.
-- Частичный индекс — NULL vk_post_id не участвуют.
CREATE UNIQUE INDEX IF NOT EXISTS uq_ad_publications_community_post
    ON ad_publications (community_vk_id, vk_post_id)
    WHERE vk_post_id IS NOT NULL;

COMMIT;
