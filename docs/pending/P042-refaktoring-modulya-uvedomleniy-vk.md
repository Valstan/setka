# Рефакторинг модуля уведомлений VK (этапы 1-5)

> Запись `P042` реестра [PENDING_FOLLOWUPS](../PENDING_FOLLOWUPS.md). Индекс грепается, запись читается целиком, реестр целиком не читается никогда ([D-097](../../AGENTS.md)).

Все этапы закрыты:

2026-05-21:
- 0 — Fallback на user-token при VK error 15/27 + `keep_if_empty` в storage.
- 1 — Полный сбор комментариев: пагинация по offset, thread.items, `max_total_comments` 300→5000 safety cap.
- 2 — BaseVKChecker (DRY), удалён UnifiedNotificationsChecker и dead-code, окно 8-22 только в crontab.
- 3 — Storage history + виджет «активность за 24ч» (Chart.js) + API `/history` `/stats`.
- 4a-mini — `like_comment` от имени сообщества, mark-as-handled (7d), виджет «Горячие посты».
- 5 — Prometheus метрики + token-health watchdog с Telegram-alert и 6h cooldown.
- hot-fix-2 — VKClient.api_call propagates error_code + regex fallback в `_invoke`.

2026-05-22:
- 4b — Inline-reply на коммент (`wall.createComment` + `from_group`); AI-черновик через Groq (`llama-3.1-8b-instant`); шаблонные ответы на сообщения + CRUD-страница `/templates` + миграция 008; Telegram inline-кнопки с deep-link на `#section=...`. Доделано всё, что откладывалось.

Техдолги по audit'у token routing (продолжаются):
