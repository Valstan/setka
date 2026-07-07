-- 055: HITL-классификатор — reviewed_at (финализация ленты) + чистка ложных коррекций.
--
-- Проблема (обнаружено 2026-07-07 при разборе живой ленты):
--   1. Лента прятала пост после ЛЮБОЙ реакции (only_unreacted = «есть хоть одна
--      коррекция»). Оператор не мог внести СОСТАВНОЙ вердикт (сменить тему И
--      действие) — карточка исчезала после первого клика.
--   2. Клик «→ публиковать» на посте, где ИИ уже поставил publish, писался как
--      ложная коррекция (outcome=correct, ai==operator) и занижал agree-rate
--      действия (60% вместо реального — 9 из 19 «коррекций» были ai==operator).
--
-- Решение: пост уходит из ленты только по reviewed_at (явная финализация —
-- «Согласен со всем» / «Готово»), а коррекция с operator==ai теперь пишется как
-- agree (правка в modules/classifier/service.py). Здесь — колонка + бэкфилл.
--
-- Идемпотентно. Откат: ALTER TABLE content_classifications DROP COLUMN reviewed_at;
-- (бэкфилл agree-rate необратим, но лишь исправляет ошибочные метки — не теряет данных).

ALTER TABLE content_classifications ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMP NULL;

-- Бэкфилл финализации: посты, уже разобранные под старой моделью (есть ≥1 реакция),
-- считаем финализированными, чтобы они не всплыли в ленте заново.
UPDATE content_classifications c
SET reviewed_at = NOW()
WHERE reviewed_at IS NULL
  AND EXISTS (
    SELECT 1 FROM classification_corrections cc WHERE cc.classification_id = c.id
  );

-- Чистка исторических ложных коррекций: оператор нажал кнопку, совпавшую с ИИ
-- (напр. «→ публиковать» на publish) → это согласие, не правка. Исправляем метку,
-- чтобы agree-rate по типам отражал реальность.
UPDATE classification_corrections
SET outcome = 'agree'
WHERE outcome = 'correct' AND ai_value = operator_value;
