-- 023: vk_tokens.role — UI-override роли «использовать для публикаций».
--
-- Контекст. До этой миграции набор user-токенов, которым разрешено публиковать
-- (wall.post / wall.repost / wall.createComment / messages.send), задавался ТОЛЬКО
-- статическим env-whitelist'ом ``VK_PUBLISH_TOKEN_NAMES`` (см.
-- ``config.runtime.get_publish_token_names``). Чтобы добавить второго публикатора
-- (например OLGA как fallback к VALSTAN), приходилось править ``/etc/setka/setka.env``
-- и рестартить сервисы. Хотелось — галочкой в ``/tokens``.
--
-- Поле:
--   * role VARCHAR(20) NULL — роль токена. Сейчас распознаётся единственное
--     значение ``'publish'`` (этому user-токену разрешено публиковать). NULL —
--     роль не задана. Семантика АДДИТИВНАЯ: ``TokenPolicy.pick`` объединяет
--     env-whitelist с множеством токенов, у которых role='publish' в БД. То есть
--     роль только РАСШИРЯЕТ список публикаторов и не меняет существующее
--     env-поведение (нулевая регрессия). Hard deny-list
--     ``VK_NEVER_PUBLISH_TOKEN_NAMES`` по-прежнему имеет приоритет над ролью.
--
-- Применяется только к user-токенам (``community_id IS NULL``); community-токены
-- публикуют в свою группу независимо от роли.
--
-- Идемпотентна: ``ADD COLUMN IF NOT EXISTS``.

ALTER TABLE vk_tokens
    ADD COLUMN IF NOT EXISTS role VARCHAR(20) NULL;
