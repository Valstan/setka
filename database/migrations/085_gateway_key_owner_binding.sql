-- 085: D-047 — привязка ключей VK-шлюза к разрешённым владельцам
-- (решение владельца 25.08, mandate brain 2026-08-25).
--
-- Колонки: allowed_owner_ids (JSON list[int], подписанные VK owner_id) и
-- allowed_screen_names (JSON list[str], lowercase). NULL/[] = ключ не привязан →
-- отказ по owner-scoped методам (modules/gateway/scope.py, fail-closed).
--
-- Посев — по замеру фактических вызовов из gateway_requests за окно ретеншена
-- (90 дн., замер 2026-08-26) + резолву screen names через VK. Screen names
-- привязанных сообществ сеем вместе с id: переход потребителя с числа на имя
-- своей же группы не должен ловить 403.
-- SABANTUY_MALMYZH сознательно НЕ привязан: ни одного вызова, сообщество не
-- подтверждено (кандидат -213609985 «САБАНТУЙ-КАЗАНСКАЯ» — ждёт владельца).
--
-- UPDATE-ы под guard'ом `allowed_owner_ids IS NULL`: повторный прогон (restore,
-- свежий клон) не перетирает позднейшие операторские правки привязок обратно
-- в посев августа-2026 (fill-if-empty, adversarial-ревью 2026-08-26).
--
-- DO-блок в конце — громкий провал вместо молчаливого промаха: UPDATE по имени,
-- которого нет в gateway_keys (опечатка, ключ жил только в env), затронул бы
-- 0 строк и закоммитился — а живой потребитель после рестарта ловил бы 403 на
-- каждом вызове. Здесь такой исход роняет транзакцию целиком.
--
-- Применять с ON_ERROR_STOP=1.

BEGIN;

ALTER TABLE gateway_keys ADD COLUMN IF NOT EXISTS allowed_owner_ids JSON;
ALTER TABLE gateway_keys ADD COLUMN IF NOT EXISTS allowed_screen_names JSON;

UPDATE gateway_keys SET
    allowed_owner_ids = '[-218991929]'::json,
    allowed_screen_names = '["kalinino_sdk"]'::json
WHERE name = 'CDK_KALININO' AND allowed_owner_ids IS NULL;

UPDATE gateway_keys SET
    allowed_owner_ids = '[-217788511]'::json,
    allowed_screen_names = '["dk_malmyzh"]'::json
WHERE name = 'DK_MALMYZH' AND allowed_owner_ids IS NULL;

UPDATE gateway_keys SET
    allowed_owner_ids = '[-226176537, -218688001, -229392127, -235385532, -229001043, 86086407]'::json,
    allowed_screen_names = '["club226176537", "gonba_life", "vyatska_lepota", "vyatka_pearl", "vyatskiy_sbor"]'::json
WHERE name = 'GONBA' AND allowed_owner_ids IS NULL;

UPDATE gateway_keys SET
    allowed_owner_ids = '[-86517261, -217788511, -213609985]'::json,
    allowed_screen_names = '["malmiz", "dk_malmyzh", "malm4317sabantuikazanskaya"]'::json
WHERE name = 'KAZANSKAYA' AND allowed_owner_ids IS NULL;

UPDATE gateway_keys SET
    allowed_owner_ids = '[-158787639]'::json,
    allowed_screen_names = '["malmig_info"]'::json
WHERE name = 'VMALMYZHE' AND allowed_owner_ids IS NULL;

UPDATE gateway_keys SET
    allowed_owner_ids = '[-195583920]'::json,
    allowed_screen_names = '["rmz43"]'::json
WHERE name = 'RMZ' AND allowed_owner_ids IS NULL;

-- Громкая приёмка посева: каждое из шести имён обязано существовать И нести
-- непустую привязку (свежий посев либо более позднюю операторскую — обе ок).
DO $$
DECLARE
    missing TEXT;
BEGIN
    SELECT string_agg(expected.name, ', ') INTO missing
    FROM (VALUES
        ('CDK_KALININO'), ('DK_MALMYZH'), ('GONBA'),
        ('KAZANSKAYA'), ('VMALMYZHE'), ('RMZ')
    ) AS expected(name)
    LEFT JOIN gateway_keys gk ON gk.name = expected.name
    WHERE gk.name IS NULL OR gk.allowed_owner_ids IS NULL;
    IF missing IS NOT NULL THEN
        RAISE EXCEPTION '085 seed missed keys: % — check gateway_keys names', missing;
    END IF;
END $$;

COMMIT;
