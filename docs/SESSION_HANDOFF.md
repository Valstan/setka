# Session Handoff

> Sticky-note для непрерывности между сессиями разработки SETKA. Перезаписывается через [`/close_session`](../.claude/commands/close_session.md) — историю смотри через `git log --follow -- docs/SESSION_HANDOFF.md`.

**Status:** IDLE
**Updated:** 2026-05-27
**Branch:** main
**Last release in prod:** `e156017` (PR #68 — TokenPolicy с авто-fallback). 3/3 сервиса setka + prometheus + grafana = 5/5 active, health 200 в 1.07s.

---

## Текущая нитка

_Нет — последняя задача закрыта, открытая стартовая позиция._

Сессия 2026-05-27 (первая в день) посвящена одной большой задаче — переделать routing VK-токенов так, чтобы:
- Vita НИКОГДА не публиковала (deny-list).
- При недоступности Valstan система автоматически переключалась на следующего кандидата.
- Был manual disable/enable через UI + REST.

Сделано одним PR:

- [PR #68](https://github.com/Valstan/setka/pull/68) `feat(tokens): TokenPolicy с авто-fallback и cooldown по VK error 5/17/29` — миграция 014 + полноценный `TokenPolicy` (READ/COMMUNITY_WRITE/USER_WRITE) + ротация по error 5/17/29 + Telegram-alert + manual disable/enable + UI кнопки на `/tokens` + интеграция во все 5 горячих путей (parsing_tasks / discovery_tasks / parsing_scheduler_tasks / copy_setka / kirov_oblast_digest / web/api/notifications). Vita защищена deny-list'ом в 3 слоях: env, runtime helpers, TokenPolicy.pick.

**Релиз на прод 2026-05-27 ~10:00:** git pull → migrate 014 (поля `disabled_until` / `last_error_code` / `last_error_at` / `consecutive_errors` в `vk_tokens`) → restart 3 сервисов → health 200 в 1.07s → `POST /api/tokens/VALSTAN/disable?hours=24` → Valstan disabled до **2026-05-28T06:59:03**.

**Что работает прямо сейчас на проде:**
- Парсинг (wall.get, groups.search и пр.) — автоматически через Vita (Valstan skip'нут по `disabled_until`).
- Notifications check (wall.getComments, messages.getConversations) — `via=community-token` в логе worker'а.
- wall.post дайджеста — пойдёт через community-токен группы (через `VKPublisher.create_with_policy`); если у группы community-токена нет — будет ошибка «no publish-token available» (нормально, Valstan в cooldown).
- wall.repost (copy_setka хаб) — недоступен до восстановления Valstan (VK API не принимает community-токен для wall.repost).

Тесты: **569/569** (+24 новых: 10 на env-helpers deny-list, 10 на TokenPolicy.pick/report/disable, 4 на VKPublisher ротацию по error 5).

## Следующий шаг

Открытой стартовой позиции нет. Кандидатные стартовые точки (по приоритету):

- **Проверить статус Valstan ~2026-05-28 07:00.** После истечения cooldown'а `disabled_until` автоматически перестаёт работать (SQL-фильтр `disabled_until > now()`). Если VK сам снял бан — токен снова в работе без действий. Если VK вернёт error 5 на первом же вызове — TokenPolicy сам поставит ещё 24ч и пришлёт Telegram-alert. Никаких manual действий не нужно, но проверить статус через `curl -s http://127.0.0.1:8000/api/tokens/VALSTAN | jq` или Kombo-кнопкой «Включить сейчас» на `/tokens` — полезно.
- **🟡 Если Valstan не вернётся в адекватный срок** — добавить второго user-token в whitelist для USER_WRITE (wall.repost). Например OLGA если она получит wall scope, или новый аккаунт. Тогда copy_setka снова заработает без правки кода: `VK_PUBLISH_TOKEN_NAMES="VALSTAN,OLGA"` в `/etc/setka/setka.env` + `systemctl restart setka setka-celery-worker` + `INSERT INTO vk_tokens (name, token, is_active) VALUES ('OLGA', '...', true)` (если ещё не в БД).
- **🟡 kirov_obl (oblast) — `dead`.** Старая нитка из handoff #67: 0/174 успехов за 30 дней, `total_groups_checked=0`. Падает на preconditions в [modules/kirov_oblast_digest.py:132](../modules/kirov_oblast_digest.py:132). Действие: запустить таску с `LOG_LEVEL=DEBUG` и посмотреть `debug_counters`.
- **🟡 `setka_digest_published_total` пуст** несмотря на успешные публикации. Beat-таски не вызывают `track_digest_published()`. Поднять `logger.debug → warning` в [tasks/parsing_scheduler_tasks.py:281,336](../tasks/parsing_scheduler_tasks.py).
- **🟢 UI поле «соседи»** в `region_new.html` (Region.neighbors есть в БД, нет в UI). Маленький PR ~30 строк.
- **🟢 `modules/publisher/neighbor_sharing.py` (dead code)** — реанимировать или удалить.

## Контекст

- **План:** нет активного.
- **Связанные коммиты сессии (1 PR):**
  - `e156017` ([PR #68](https://github.com/Valstan/setka/pull/68)) — feat(tokens): TokenPolicy с авто-fallback (+1521/-299, 13 файлов, +24 тестов).
- **Прод:** HEAD на `e156017`, 5/5 сервисов active (setka + celery-worker + celery-beat + prometheus + grafana). Миграция 014 применена. Health 200 в 1.07s. **Valstan disabled** до 2026-05-28T06:59:03 (24ч cooldown через `/api/tokens/VALSTAN/disable`). Vita active.
- **Открытых PR:** нет (handoff-PR создаётся этим вызовом `/close_session`).

## Открытые вопросы для пользователя

_Нет._

## Не забыть (low-priority)

- 🟢 **Через ~24ч (2026-05-28 ~07:00) — посмотреть `/tokens` или `curl /api/tokens/VALSTAN`.** Если `disabled_until` уже в прошлом — токен снова работает; если попал в auto-disable (Telegram-alert) — VK всё ещё блокирует, добавить второй publish-token или ждать дальше.
- 🟢 **Grafana через nginx-proxy с basic-auth.** Из прошлой сессии — пользователь выбирал этот вариант наряду с виджетом на /monitoring. Виджет сделан 2026-05-26, Grafana-proxy отложен.
- 🟢 **node_exporter** для host-level метрик в Grafana. ~50MB RAM.
- 🟢 **Grafana admin/admin** — при первом входе Grafana просит сменить, можно «Skip».

---

> Если читаешь это в начале новой сессии — обнови через `/close_session` в конце. **Не аккумулируй history тут** — она в `git log --follow -- docs/SESSION_HANDOFF.md` (и в `git log` основной ветки для коммитов вообще; `DEV_HISTORY.md` упразднена с 2026-05-24, см. [ADR-0001](adr/0001-archive-dev-history.md)).
