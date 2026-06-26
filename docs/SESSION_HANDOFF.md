# Session Handoff

> Sticky-note для непрерывности между сессиями разработки SETKA. Перезаписывается через [`/close_session`](../.claude/commands/close_session.md) — историю смотри через `git log --follow -- docs/SESSION_HANDOFF.md`.

**Status:** ACTIVE (mid-flight кода нет; VK-шлюз v1 построен и задеплоен; открытая brain-директива deliver-19 ждёт)
**Updated:** 2026-06-26
**Branch:** main
**Last release in prod:** прод HEAD `0f844e4` (#287). Все сервисы active, health 200 (restart 2026-06-26 23:31 MSK). **Миграция 049 применена.** В `/etc/setka/setka.env` добавлены 5 ключей `GATEWAY_KEY_*`. Прод-правка nginx вне git в силе: `client_max_body_size 20m`.

---

## Текущая нитка

**VK-шлюз — ворота доступа в VK для других проектов @valstan** (запрос владельца 2026-06-26). SARAFAN
исполняет read-задачи VK своим токеном (со своего IP, под своим rate-limit/cooldown) и возвращает JSON
другим проектам — токен наружу не уходит. **v1 read-only построен, задеплоен, проверен end-to-end.**

За сессию (4 PR, все merged + в проде):

1. **Шлюз** ([#284](https://github.com/Valstan/setka/pull/284), `f999eb5`): роутер `/api/gateway`
   (`POST /call` allowlist read-методов + `GET /community` + `GET /wall`). Auth — API-ключ на проект
   (`X-API-Key`, constant-time), квота на ключ (Redis fixed-window, fail-open), переиспользует `TokenPolicy`
   (cooldown 5/17/29) + `VKClient`. Конфиг `config/gateway.py`, kill-switch `GATEWAY_DISABLED`.
2. **Контракт** ([#285](https://github.com/Valstan/setka/pull/285), `6e84af1`): `docs/GATEWAY.md` с
   прод-доменом `3931b3fe50ab.vps.myjino.ru`.
3. **Статистика** ([#286](https://github.com/Valstan/setka/pull/286), `2de23ca`, миграция 049): таблица
   `gateway_requests` + лог `modules/gateway/usage.py` (кто/когда/метод/параметры/результат) + операторский
   API `/api/gateway-stats` + страница `/gateway-stats` (таблица по проектам + график + лента запросов).
4. **Отметки деплоя** ([#287](https://github.com/Valstan/setka/pull/287), `0f844e4`).

## Следующий шаг

Открытой кодовой нити нет (шлюз v1 завершён и задеплоен). Кандидаты (по приоритету):

1. **🔴 brain deliver-19** (high SHOULD, `2026-06-26-spotcheck-bump-deliver-19.md`) — отдать 19
   LLM-drop-флагов спот-чека **простым списком** (19 строк: пост-ссылка + рубричная причина) из
   shadow-таблицы `digest_curation_runs` (регион Малмыж, статы `2026-06-14-llm-curation-poc-stats.md`).
   **Единственный блокер** промоута фильтра LLM-курации; владелец готов пройти спот-чек. Висит 11+ дней.
   Отдать письмом в `mailbox/to-brain/` или committed-артефактом. **Топ-приоритет от brain.**
2. **VK-шлюз v2-бэклог** (PENDING): запись в VK (guarded, per-key scope); async-джоба для тяжёлого
   «прочесать весь паблик»; логировать 401/429 на странице статистики; ретеншн `gateway_requests`;
   MCP-обёртка для проектов-потребителей.
3. **И5 «всё в карточке»** (непрерывная нить клиента) — планировать публикацию + видеть заявки из карточки
   клиента overlay-модалкой. Бэкенд `POST /clients/{id}/schedule-here` + `GET /clients/{id}/requests`.

## Контекст

- **План:** дизайн-файл шлюза `~/.claude/plans/elegant-watching-axolotl.md` (локальный, исполнен).
- **Связанные коммиты сессии:** `f999eb5` (#284 шлюз), `6e84af1` (#285 контракт), `2de23ca` (#286 статистика+миграция 049), `0f844e4` (#287 деплой-отметки).
- **Прод:** все сервисы active, health 200, HEAD `0f844e4` = origin/main. Миграция 049 применена. 5 ключей в env.
- **Открытых PR:** этот handoff-PR (doc-only + письмо-находка, авто-merge). #284–287 смержены и задеплоены.

## Failed approaches (этой нитки)

- **Выдавать VK-токен потребителю** (модель «дай credential, проект сам сходит в VK») — отвергнута: VK
  привязывает user-токен к IP выпуска → чужой проект со своего сервера получит `error 5 (access_token was
  given to another ip address)`. Технически не работает. Принят паттерн «исполни задачу и верни результат»
  (токен не покидает SARAFAN). Зафиксировано в `mailbox/to-brain/2026-06-26-project-as-vk-gateway-shared-service.md`.

## Открытые вопросы для пользователя

- deliver-19 — делаем следующим (топ-приоритет brain), или сначала что-то ещё?
- VK-шлюз v2 — что первым из бэклога, если возвращаемся к шлюзу?

## Не забыть (low-priority)

- 📬 Письмо-находка про паттерн «проект-как-VK-шлюз» ушло в `mailbox/to-brain/2026-06-26-project-as-vk-gateway-shared-service.md` (в этом закрывающем PR).
- 🟢 **Раздать ключи потребителям** (действие владельца): `ssh setka "sudo grep '^GATEWAY_KEY_' /etc/setka/setka.env"` → вставить в Сабантуй Малмыж / Вмалмыже.рф / ЦДК-Калинино.рф / ДкМалмыж.рф / Гоньба.
- 🟢 **Браузер-проверка** владельцем страницы `/gateway-stats` (меню «Система»).
- ⚠️ **Грабля деплоя (G92):** `ssh "systemctl restart"` может молча НЕ выполниться — проверять `ActiveEnterTimestamp` свежее времени pull, не только health 200. В этой сессии оба деплоя проверены.
- 🟢 Следующий dead-code прогон ~2026-07-14.

---

> Если читаешь это в начале новой сессии — обнови через `/close_session` в конце. **Не аккумулируй history тут** — она в `git log --follow -- docs/SESSION_HANDOFF.md`.
