-- 060: сводка вложений в аудите сбора — классификатор смотрит медиа постов без текста.
--
-- collected_post_audit.media JSONB: [{type, url?, ext?, title?}] — у фото прямая
-- ссылка на лучший размер, у документов url+ext, у видео/аудио/ссылок только тип.
-- Пишется fail-safe рекордером сбора (modules/curation/collection_audit.py);
-- облачная рутина скачивает фото/PDF через media-прокси /api/classifier/media
-- (egress облака пускает только наш хост) и выносит вердикт с media_summary.
--
-- Аддитивная, без backfill (старые записи NULL = «вложения не снимались»).
-- Откат: ALTER TABLE collected_post_audit DROP COLUMN media;

ALTER TABLE collected_post_audit
    ADD COLUMN IF NOT EXISTS media JSONB;
