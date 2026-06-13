-- 041: авто-удаление рекламных постов по сроку (С2 программы ad-CRM, директива
-- brain 2026-06-12). Срок размещения задаётся при планировании (опционально):
-- поле expires_at на отложке вычисляется из «N дней от публикации» или явной
-- даты снятия. Ежедневная beat-таска expire-ad-posts-daily снимает вышедшие
-- посты (wall.delete) по истечении срока.
--
-- expires_at хранится как МСК wall-clock naive (как publish_date) — снятие
-- сравнивается с МСК-now. removed_at — момент фактического удаления (UTC).
-- Срок копируется в ad_publications при авто-фиксации публикации (reconciler).
-- Решения владельца 2026-06-13: срок опционален (нет срока → висит вечно),
-- снимаем по сроку независимо от оплаты, тихо + запись в таймлайн.
--
-- Идемпотентна: ADD COLUMN / CREATE INDEX IF NOT EXISTS.

ALTER TABLE ad_scheduled_posts ADD COLUMN IF NOT EXISTS expires_at TIMESTAMP NULL;

ALTER TABLE ad_publications ADD COLUMN IF NOT EXISTS expires_at TIMESTAMP NULL;
ALTER TABLE ad_publications ADD COLUMN IF NOT EXISTS removed_at TIMESTAMP NULL;

-- Ускоряет ежедневную выборку «вышедшие посты с истёкшим сроком».
CREATE INDEX IF NOT EXISTS ix_ad_publications_expiry
    ON ad_publications (status, expires_at);
