-- 017: нормализация знака regions.vk_group_id (инвариант — отрицательный owner_id).
--
-- Контекст. Колонка `regions.vk_group_id` хранит VK owner_id главной группы
-- региона в owner-форме — для групп это ОТРИЦАТЕЛЬНОЕ число (как -168170001).
-- 16 из 17 регионов так и записаны, но `tuzha` исторически попал с
-- положительным 239050321 (модератор ввёл «голый» id в /regions, где до этого
-- PR не было нормализации на входе).
--
-- Рантайм-публикация от этого НЕ ломается: весь publish/token-routing путь уже
-- defensively нормализует знак — VKPublisher._normalize_group_owner_id = -abs,
-- TokenPolicy.pick(group_id=...) берёт abs, get_wall_posts(-abs(int(...))).
-- Поэтому это не блокер, а починка инварианта: положительный id сбивает любой
-- код/SQL, который сравнивает vk_group_id напрямую (без abs), и противоречит
-- остальным 16 регионам. В этом же PR добавлен Pydantic-валидатор в
-- web/api/regions.py (_to_negative_owner_id), чтобы положительный id больше не
-- мог попасть в БД из UI/API.
--
-- Идемпотентна: WHERE vk_group_id > 0 — повторное применение no-op.

UPDATE regions
SET vk_group_id = -abs(vk_group_id),
    updated_at = NOW()
WHERE vk_group_id IS NOT NULL
  AND vk_group_id > 0;
