# Наблюдаемость / UI

> Запись `P004` реестра [PENDING_FOLLOWUPS](../PENDING_FOLLOWUPS.md). Индекс грепается, запись читается целиком, реестр целиком не читается никогда ([D-097](../../AGENTS.md)).

- ~~**Счётчики «Главные/Вспомогательные токены: 0» на `/tokens`**~~ Закрыто 2026-06-07 (ветка `fix/tokens-main-aux-counters`): плашки были жёстко зашиты в `0` с устаревшим комментарием «Token type is not stored in DB model» — хотя `community_id` есть в модели с миграции 007. Введена чистая `web/api/token_management.compute_token_stats()` (main = валидные user-токены, aux = валидные community-токены `COMM_*`, broken = любые невалидные; разбиение `main+aux+broken==total`) + эндпоинт `GET /api/tokens/stats`; `updateStatistics` в `tokens.html` дёргает его (клиентский fallback при сбое). +6 тестов. Без миграции, деплой — restart `setka` (web). _Браузер-верификация за владельцем._
