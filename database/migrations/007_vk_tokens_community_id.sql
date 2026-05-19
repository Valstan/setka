-- 007: per-community VK access tokens for messages.getConversations
--
-- VK ограничивает scope `messages` для user-токенов (выдаётся только аппам,
-- прошедшим VK security review). Чтобы читать диалоги сообщества, в проде
-- удобнее использовать community access tokens, которые выдаются в
-- vk.com/club{ID} -> Управление -> Работа с API -> Создать ключ.
--
-- Этот столбец привязывает запись `vk_tokens` к конкретному сообществу:
--   community_id IS NULL  -> user-token (как было)
--   community_id IS NOT NULL -> community-token для группы с этим vk_group_id.
--
-- Хранится как abs(group_id) (положительное число), чтобы было удобно join'ить
-- с regions.vk_group_id (там встречаются и положительные, и отрицательные ID).

ALTER TABLE vk_tokens
    ADD COLUMN IF NOT EXISTS community_id BIGINT;

CREATE INDEX IF NOT EXISTS idx_vk_tokens_community_id
    ON vk_tokens(community_id)
    WHERE community_id IS NOT NULL;
