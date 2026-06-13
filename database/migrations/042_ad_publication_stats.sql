-- 042: метрики рекламных публикаций (С3 программы ad-CRM, директива brain
-- 2026-06-12). Сбор просмотров/лайков/репостов вышедших рекламных постов через
-- wall.getById (переиспользуем стат-стек modules/vk_monitor). Решение владельца
-- 2026-06-13: метрики = просмотры + лайки + репосты; авто раз в день + кнопка
-- «Обновить»; показ оператору в CRM + отчёт клиенту.
--
-- NULL = метрика ещё не собрана (UI показывает «—»); stats_updated_at — когда
-- последний раз тянули. Заполняет beat-таска collect-ad-publication-stats-daily
-- и кнопка ручного обновления (modules/ad_cabinet/publication_stats.py).
--
-- Идемпотентна: ADD COLUMN IF NOT EXISTS.

ALTER TABLE ad_publications ADD COLUMN IF NOT EXISTS views INTEGER NULL;
ALTER TABLE ad_publications ADD COLUMN IF NOT EXISTS likes INTEGER NULL;
ALTER TABLE ad_publications ADD COLUMN IF NOT EXISTS reposts INTEGER NULL;
ALTER TABLE ad_publications ADD COLUMN IF NOT EXISTS stats_updated_at TIMESTAMP NULL;
