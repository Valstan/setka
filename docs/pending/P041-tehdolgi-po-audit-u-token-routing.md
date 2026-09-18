# 🆕 Техдолги по audit'у token routing

> Запись `P041` реестра [PENDING_FOLLOWUPS](../PENDING_FOLLOWUPS.md). Индекс грепается, запись читается целиком, реестр целиком не читается никогда ([D-097](../../AGENTS.md)).

Закрыто 2026-05-21 (см. `DEV_HISTORY.md`):
- ✅ VK Captcha rate-limit — добавлен `GLOBAL_PUBLISH_INTERVAL_SECONDS=1.5` (class-var на VKPublisher).
- ✅ Косметика лога `via=community-token` после fallback — `_call_wall_post` теперь возвращает `(response, via_label)`, метка вычисляется по фактическому пути.
- ✅ wall.repost больше не пробует community-token (VK API физически не поддерживает) — добавлен `_USER_TOKEN_ONLY_METHODS={'wall.repost'}`.

Закрыто 2026-05-22 (см. `DEV_HISTORY.md`, этап 4b):
- ✅ Inline-reply на коммент из карточки `/notifications`.
- ✅ AI-черновик через Groq в модалке ответа (кнопка ✨).
- ✅ Шаблонные ответы на сообщения сообщества + CRUD-страница `/templates`.
- ✅ Telegram inline-кнопки с deep-link на `/notifications#section=...`.

~~Остаются:~~ **Не остаётся ничего — заголовок снят 2026-08-28 по ре-триажу.** Все три пункта
ниже зачёркнуты ещё 2026-05-22, а единственный живой хвост из них разрешён в «🟢 Идеи»:
«~~Мигрировать `web/api/publisher.py` на extended VKPublisher.~~ Уже мигрировано… Запись была
устаревшей» — подтверждается кодом: `web/api/publisher.py:23`
`from modules.publisher.vk_publisher_extended import VKPublisher`, и методы `get_group_info`
(`:138`), `get_target_group_id` (`:208`), `publish_aggregated_post` (`:242`) вызываются именно
на нём. Cross-process rate-limit тоже существует — `modules/vk_monitor/rate_limiter.py`.
Секция ниже оставлена историческим record'ом рядом с закрытыми этапами 0–5.

- ~~**Удалить мёртвый код**~~ (закрыто 2026-05-22, см. DEV_HISTORY): `cross_region_repost.py` оказался уже удалён ранее; `correct_workflow` + `publish_bulletin_to_main_group` удалены целиком вместе с beat-entry `monitoring-hourly` и `tasks/correct_workflow_tasks.py`.
- ~~**Мигрировать старый `vk_publisher.py`**~~ (частично закрыто 2026-05-22): deprecated стек удалён (`modules/publisher/publisher.py`, `modules/scheduler/scheduler.py`, `tasks/publishing_tasks.py`, `tasks/test_info_tasks.py`, `modules/test_info_scheduler.py`, `web/api/workflow.py`, `scripts/test_full_workflow.py`). Остаётся живой `web/api/publisher.py` (UI `/publisher`) — он использует кастомные методы (`get_group_info`, `publish_aggregated_post`, `get_target_group_id`), которых нет в extended. Миграция требует либо расширения extended-API, либо переписывания endpoint'ов. Записано в 🟢 идеи.
- ~~**Глобальный rate-limit на parse-token VITA**~~ — закрыто 2026-05-22 (`GLOBAL_PARSE_INTERVAL_SECONDS=0.4` в `VKClient`, per-process per-token). Cross-process variant (через Redis) на случай multi-worker Celery — записан в 🟢 идеи.

_Все запланированные этапы (0, 1, 2, 3, 4a-mini, 4b, 5) закрыты. См. `DEV_HISTORY.md`._

---
