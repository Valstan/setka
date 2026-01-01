# Модель данных SETKA (PostgreSQL)

Источник истины: `database/models.py`.

## Основные сущности

### `regions`

Ключевые поля:
- `code` — короткий код региона (`mi`, `nolinsk`, …)
- `name` — отображаемое имя
- `vk_group_id` — главная VK-группа региона
- `telegram_channel` — канал региона (опционально)
- `neighbors` — строка с соседями (опционально)
- `config` — JSON-настройки региона (например шаблон дайджеста)
- `is_active`

Связи:
- `regions (1) -> (many) communities`
- `regions (1) -> (many) posts`

### `communities`

Ключевые поля:
- `region_id` — FK на `regions`
- `vk_id` — отрицательный id сообщества VK (owner_id)
- `screen_name`, `name`
- `category` — категория источника (например `novost`, `kultura`, `sport`, `admin`, …)
- `is_active`

### `posts`

Ключевые поля:
- `region_id`, `community_id` — FK
- `vk_owner_id`, `vk_post_id`
- `text`, `attachments` (JSON)
- `views/likes/reposts/comments`
- AI: `ai_category`, `ai_relevance`, `ai_score`, `ai_analyzed`, `ai_analysis_date`
- Sentiment: `sentiment_label`, `sentiment_score`, `sentiment_emotions` (JSON)
- Publishing: `status`, `published_vk`, `published_telegram`, `published_wordpress`
- Fingerprints: `fingerprint_lip`, `fingerprint_media`, `fingerprint_text`, `fingerprint_text_core`

### `vk_tokens`

Токены VK, управляемые через API/UI.

Ключевые поля:
- `name`
- `token` (полный, в API маскируется)
- `is_active`, `validation_status`, `last_used`, `last_validated`
- `permissions`, `user_info` (JSON)

### `filters`

Ключевые поля:
- `type`, `category`
- `pattern`
- `action`, `score_modifier`
- `is_active`

### `publish_schedules`

Ключевые поля:
- `region_id`
- `category`
- `hour`, `minute`
- `days_of_week`
- `is_active`


