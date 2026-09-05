-- 099: закреп рекламного поста на сутки (Этап 2, PR 2C; решение владельца 2026-09-05).
--
-- ЗАЧЕМ. Прайс обещает «закреп на сутки +200 ₽», а в кабинете и учёте закрепа
-- не было. Теперь строка отложки несёт флаг pinned (цена +200 ₽ за сообщество,
-- пакетом и скидками не покрывается), после выхода реконсилер делает wall.pin,
-- публикация запоминает pinned_at/pinned_until, задача unpin-ad-posts снимает
-- закреп через сутки и ставит unpinned_at.
--
-- Идемпотентна (IF NOT EXISTS). Таймстампы — UTC naive, как published_at.
-- Без BEGIN/COMMIT — их даёт раннер.

ALTER TABLE ad_scheduled_posts ADD COLUMN IF NOT EXISTS pinned BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE ad_publications ADD COLUMN IF NOT EXISTS pinned_at TIMESTAMP NULL;
ALTER TABLE ad_publications ADD COLUMN IF NOT EXISTS pinned_until TIMESTAMP NULL;
ALTER TABLE ad_publications ADD COLUMN IF NOT EXISTS unpinned_at TIMESTAMP NULL;
CREATE INDEX IF NOT EXISTS ix_ad_pub_pin_due
    ON ad_publications (pinned_until)
    WHERE pinned_until IS NOT NULL AND unpinned_at IS NULL;
COMMENT ON COLUMN ad_scheduled_posts.pinned IS 'Закреп на сутки после выхода (+200 ₽ за сообщество)';
