# История разработки SETKA

Хронология значимых изменений проекта SETKA. **Свежее сверху.** Каждый блок — одна сессия разработки (день) или один логически законченный кусок.

При обновлении: новый блок ставится в самый верх под заголовком, с датой в формате `YYYY-MM-DD` и кратким заголовком задачи. Что меняли (файлы), зачем, какие тесты, какие хвосты ушли в [`PENDING_FOLLOWUPS.md`](PENDING_FOLLOWUPS.md).

<details>
<summary>Шаблон новой записи</summary>

```markdown
## YYYY-MM-DD — Короткий заголовок задачи

**Тема сессии:** один абзац контекста — зачем взялись, что было сломано.

### Изменения

- **`path/to/file.py`** — что и зачем. Если миграция БД — ссылка на `database/migrations/NNN_*.sql`.
- **`web/api/...`** — изменения API.
- **`tests/...`** — какие тесты добавили/обновили (N зелёных).

### Проверка / прогон

- Локально: `pytest tests/ -q` — N/N зелёных.
- На проде: `ssh setka-prod "..."` или ссылка на `/reliz`-флоу.

### Хвосты, оставленные в `PENDING_FOLLOWUPS.md`

- 🟡 ...
- 🟢 ...
```

</details>

---

## 2026-05-21 — Token routing fix: wall.repost минует community, глобальный rate-limit на VALSTAN

**Тема сессии:** пользователь спросил почему дайджесты публикуются через VALSTAN, а не через community-tokens регионов. Аудит показал: **дайджесты (wall.post) УЖЕ идут через community-tokens** (см. лог 14:05-14:06, 10 групп подряд `Published post ... (via community-token)`). Жалоба основана на косметическом баге логирования `Reposted ... (via community-token)` — после fallback там уже publish-token. Параллельно сразу две проблемы оставались:

1. **wall.repost через community-token гарантированно падает с VK error 27** — VK API физически не поддерживает group access tokens для этого метода. Сейчас на каждый запуск copy-setka делалось 13-14 заведомо-провальных VK-запросов через community-tokens, потом fallback. Это засоряло логи и подтачивало rate-limit publish-token.
2. **VK captcha [0] на publish-token** после burst'а из ~10 wall.repost'ов за 7 сек (14:37 на проде). VITA только парсит (нет admin прав), так что весь publish-traffic уходит в VALSTAN — нужен глобальный rate-limit именно на этот токен.

### Изменения

#### `modules/publisher/vk_publisher_extended.py`

- **Новая константа `_USER_TOKEN_ONLY_METHODS = frozenset({'wall.repost'})`** — список VK API методов, которые VK документировано не поддерживает с group-token. `_call_wall_post` для таких методов сразу идёт через `self.vk_client` (publish-token), не пробуя community-token. Экономит ~13 обречённых VK-запросов на каждый запуск copy-setka.
- **Новый класс-атрибут `GLOBAL_PUBLISH_INTERVAL_SECONDS = 1.5`** + class-vars `_last_publish_token_call` и `_publish_token_lock`. Метод `_enforce_publish_token_rate_limit()` гарантирует минимальный интервал между **всеми** API-вызовами через publish-token (VALSTAN), независимо от того, в каком VKPublisher-instance они происходят. 13 repost'ов теперь занимают ~20 сек вместо 7 — VK не успевает поставить капчу.
- **`_call_wall_post` теперь возвращает tuple `(response, via_label)`.** Метка via берётся по факту, какой клиент **реально** успешно выполнил запрос: `publish-token` / `community-token` / `community-fallback-publish`. Это убирает косметический баг лога «Reposted ... via community-token» после fallback.
- `publish_digest` и `publish_repost` обновлены под новую сигнатуру, логируют точный via.
- Старый класс-атрибут `_COMMUNITY_FALLBACK_CODES` поднят выше в шапку как single source of truth.

#### Audit token routing — где и как используются токены

| Место | VK API метод | Текущий маршрут | Статус |
|---|---|---|---|
| `parse_and_publish_theme` (часовые дайджесты regular/mourning) | wall.post | community → publish (fallback) | ✓ работает, в логах `via community-token` |
| `kirov_oblast_digest` (oblast novost/mourning) | wall.post | community → publish | ✓ |
| `copy_setka_network` text-copy | wall.post | community → publish | ✓ |
| `copy_setka_network` repost-mode | wall.repost | **publish-only** (этот фикс) | ✓ no более waste calls |
| `cross_region_repost.py` | wall.repost | dead code, никем не импортируется | → PENDING (удалить) |
| `correct_workflow.publish_digest_to_main_group` | (заглушка) | не публикует, только логирует | → PENDING (доделать или удалить) |
| `tasks/real_vk_workflow.py`, `web/api/publisher.py` | wall.* | используют старый `vk_publisher.py` (без community-tokens) | → PENDING (мигрировать на extended) |
| `check_suggested_posts` / `check_recent_comments` | wall.get(...) | BaseVKChecker community → user (fallback) | ✓ |
| `check_unread_messages` | messages.getConversations | BaseVKChecker community-only | ✓ |
| `like_comment` | likes.add | community → user fallback | ✓ |
| Парсинг чужих сообществ (`vk_monitor/*`) | wall.get | VKClient(VITA или VALSTAN-parse) | ✓ (community-tokens принципиально неприменимы к чужим стенам) |

Вердикт: все hot-path точки уже используют community-tokens. Старые/мёртвые пути — записаны в PENDING на следующие сессии.

### Тесты

- **`test_publisher_fallback.py`** обновлён под tuple-возврат:
  - `test_repost_skips_community_token_entirely` (новый) — community-client.method НЕ вызывается для wall.repost, сразу publish-client.
  - `test_global_rate_limit_throttles_publish_token` (новый) — два back-to-back publish-token-call отстают друг от друга на ≥ `GLOBAL_PUBLISH_INTERVAL_SECONDS` (test переустанавливает её в 0.3 для ускорения CI).
  - `test_post_fallback_on_15`, `test_fallback_on_code_27_when_only_in_error_msg`, `test_community_token_success_no_fallback` — обновлены под `(response, via)`.
- **216/216 pytest green** (215 после этапа 4a-mini + новый test_global_rate_limit, без потерь старых).

### Применение

- Деплой через `git pull` + restart. После рестарта следующий copy-setka-тик (14:37 + 30 мин = ~15:07 на момент сессии, либо 15:37) увидим:
  - В worker.log **больше нет** `VK API error (wall.repost): [27]` и `wall publish via community-token failed (code 27 on wall.repost), retrying via publish-token` — этих строк не должно быть.
  - Вместо этого: `✅ Reposted ... (via publish-token)` напрямую.
  - Между repost'ами видимая пауза ~1.5 сек.
  - 13/13 групп должны репостнуться без captcha (раньше 10/13 + 3 captcha).

### Хвосты в PENDING_FOLLOWUPS

- 🟡 Удалить `modules/publisher/cross_region_repost.py` (dead code) и заглушку `correct_workflow.publish_digest_to_main_group`.
- 🟡 Мигрировать `web/api/publisher.py` и `tasks/real_vk_workflow.py` (если он ещё нужен) с `vk_publisher.py` на extended `vk_publisher_extended.VKPublisher` с community-tokens.
- 🟡 Глобальный rate-limit аналогичный на parse-token VITA (на случай если VK добавит более жёсткий лимит и для read-операций). Сейчас vk_api lib сама делает sleep `Too many requests! Sleeping 0.5 sec`, но это per-session.

---

## 2026-05-21 — Этап 4a-mini: лайки коммента, mark-as-handled, виджет «Горячие посты»

**Тема сессии:** UI обратной связи для модераторов — три быстрые действия из карточки коммента без перехода в VK. Inline-reply / AI-черновик / шаблоны для сообщений / Telegram inline-button — **отложены в этап 4b** (требуют модального окна и больше времени).

### Изменения

#### Backend

- **`modules/notifications/vk_actions.py` (новый)** — `like_comment(owner_id, post_id, comment_id, user_token, community_tokens)`. Использует `likes.add(type='comment', ...)` через community-token приоритетно, при error 15/27 — fallback на user-token (как у read-checker'ов).
- **`modules/notifications/storage.py`** — четыре новых метода для handled-mark:
  - `mark_handled(notification_type, item_id)` — `SETEX key TTL=7d`.
  - `unmark_handled(...)` — `DELETE`.
  - `is_handled(...)` — `EXISTS`.
  - `get_handled_set(type)` — `KEYS prefix*` → set of ids.
- **`web/api/notifications.py`** — новые endpoint'ы:
  - `POST /api/notifications/handled` `{notification_type, item_id}` — отметить.
  - `DELETE /api/notifications/handled` — снять.
  - `GET /api/notifications/handled/{type}` — список handled-id для bulk-render UI.
  - `POST /api/notifications/comments/like` `{owner_id, post_id, comment_id}` — лайк.
  - `GET /api/notifications/hot-posts?min_comments=5&limit=5` — топ-5 постов с самой активной перепиской (агрегируется из уже собранных в Redis комментов, без отдельных VK-запросов; сортировка по `unhandled_comments` desc, ties → `total_comments` desc).

#### UI

- **`web/static/js/notifications.js`** — переработан `loadRecentComments`:
  - Параллельно тянет `/handled/recent_comment` и **скрывает** уже обработанные.
  - В каждой карточке коммента три новых кнопки в правом столбце: `🔗 Открыть в VK`, `🤍 Лайкнуть от имени сообщества`, `✓ Отметить обработанным`.
  - Бейджи `ответ` (для `is_reply=true`) и `🤍 N` (если `likes_count > 0`).
  - `likeComment(...)` → POST → при успехе кнопка превращается в `❤️` (filled). `markHandled(...)` → POST → карточка плавно исчезает (opacity 0.2 → remove).
- **Виджет «Горячие посты»** под виджетом активности: title region + preview первого коммента + бейдж `N 🟡 из M` (необработанных из всего). Кликабельная ссылка на пост в VK. Прячется если кандидатов нет.
- Кэш-бастинг JS: `notifications.js?v=20260521_4`.

### Тесты

- **`tests/test_notifications/test_handled_marks.py`** (4 теста): SETEX с правильным TTL, DELETE, EXISTS, KEYS-pattern → set strip-prefix.
- **`tests/test_notifications/test_vk_actions.py`** (4 теста): like через community-token (happy path), fallback на user при code 27, отсутствие community-token → сразу user, неоднозначные ошибки → failure payload без retry.
- **215/215 pytest green** (207 после этапов 3+5 + 4 handled + 4 vk_actions).

### Что НЕ вошло (отложено в этап 4b)

- Inline-ответ на коммент из SETKA (нужен модальник + `wall.createComment(reply_to_comment=...)`).
- AI-черновик через Groq (кнопка ✨ → предзаполненная textarea).
- Шаблонные ответы на сообщения сообщества — отдельный экран `/templates` для CRUD шаблонов + `messages.send` через community-token.
- Telegram inline-кнопка «Ответить из SETKA» — требует bot webhook + deep-link на `/notifications#comment_X`.

Все четыре пункта зафиксированы в `PENDING_FOLLOWUPS.md` → этап 4b.

### Применение

- Деплой через `git pull` + restart. UI обновляется автоматически при следующем заходе на `/notifications` (cache-bust v=20260521_4). Виджет «Горячие посты» появится когда наберётся ≥5 комментариев под одним постом в окне 24ч.

---

## 2026-05-21 — Этапы 3 + 5: история проверок, виджет «активность за 24ч», Prometheus + token-health alert

**Тема сессии:** дать UI окно в реальную работу автотасок (раньше единственная отметка времени — «последняя проверка», без истории) и навесить алёрт если токены реально сломались.

### Этап 3 — История проверок и виджет

#### `modules/notifications/storage.py`

- Новые методы:
  - `save_run(notification_type, *, count, duration_seconds, denied_count=0, success=True, extra=None)` — append-в-Redis-list `setka:notifications:history:{type}`, LPUSH + LTRIM до `HISTORY_MAX_ENTRIES=48` + EXPIRE `HISTORY_TTL_SECONDS=25h`. Атомарно через pipeline.
  - `get_recent_runs(notification_type, limit)` — newest-first list.
  - `get_stats()` — агрегаты по трём типам: `total_runs`, `with_results_runs`, `total_items`, `avg_duration_s`, `last_run_ts`, `last_run_count`.

#### `tasks/celery_app.py`

- Каждая из трёх auto-тасок (`check_suggested_posts`, `check_unread_messages`, `check_recent_comments`) после `save_notifications` вызывает `storage.save_run(...)` с реальным `run_duration`.

#### `web/api/notifications.py`

- Новые эндпойнты:
  - `GET /api/notifications/history?notification_type=...` (или без параметра — все три).
  - `GET /api/notifications/stats` — сводка для виджета.

#### UI

- В `web/templates/notifications.html` под "Последняя проверка" добавлен новый блок «Активность за последние 24 часа» с тремя счётчиками (предложки/сообщения/комменты — прогонов / с результатом) и линейным графиком Chart.js (по трём сериям).
- В `web/static/js/notifications.js` — `loadActivityWidget()` и `renderActivityChart()` (category-scale, без time-adapter — Chart.js 4 не требует дополнительных пакетов).
- Подключён CDN `chart.js@4.4.0`. Кэш-бастинг JS-файла: `notifications.js?v=20260521_3`.

### Этап 5 — Prometheus метрики + token-health alert

#### `monitoring/metrics.py`

- Новые метрики:
  - `setka_notifications_check_total{check_type,result}` — Counter (`check_type`: suggested/messages/comments; `result`: ok/empty/error/denied).
  - `setka_notifications_check_duration_seconds{check_type}` — Histogram.
  - `setka_notifications_items_found_total{check_type}` — Counter.
  - `setka_notifications_zero_streak{check_type}` — Gauge (для Grafana alerting и для отладки).

#### `modules/notifications/health.py` (новый)

- `detect_zero_streaks(storage)` — для каждого типа считает кол-во последних подряд auto-runs с `count==0`. Заодно обновляет Prometheus Gauge.
- `maybe_alert_broken_tokens(...)` — если streak ≥ `ZERO_STREAK_THRESHOLD=3` — отправляет Telegram-alert (HTML), с cool-down `ALERT_COOLDOWN_SECONDS=6h` через Redis-флаг чтобы не спамить.

Подключение: в конце `check_recent_comments` (последняя в hourly-цепи) вызывается `maybe_alert_broken_tokens`.

### Тесты

- **`tests/test_notifications/test_storage_history.py`** (4 теста): LPUSH+LTRIM+EXPIRE pipeline; JSON-decode для get_recent_runs; агрегация get_stats по трём типам; payload с extra-полем.
- **`tests/test_notifications/test_health_watchdog.py`** (6 тестов): streak=0 если последний прогон непустой; streak считает leading zeros; alert не шлётся ниже threshold; cooldown блокирует; alert уходит при streak ≥ threshold (Bot.send_message → cooldown setex); skipped если нет telegram-конфига.
- **207/207 pytest green** (197 после этапа 2 + 4 history + 6 health).

### Применение

- Деплой через `git pull` + restart всех трёх сервисов. Виджет начнёт заполняться по мере прохождения часовых тиков (через 1ч будет первая точка для каждой серии). UI откроется и без данных корректно (если история пустая — chart пустой, без ошибок).
- Prometheus метрики появятся на `/metrics` сразу; Grafana может строить графики; alert-rule в Grafana можно настроить на `setka_notifications_zero_streak >= 3`.

### Прод-наблюдения по ходу сессии

- В 14:37 (после деплоя hot-fix-2) на проде `wall.repost` через community-token падает с error 27 → fallback на publish-token успешно репостит 10 из 13 групп. Оставшиеся 3 группы получают `[0] Captcha needed` — VK rate-limited VALSTAN user-token после 10 repost'ов за 7 сек. **Новый техдолг:** глобальный rate-limiter на publish-token (а не per-group) или ротация на второй publish-токен (VITA). Записано в `PENDING_FOLLOWUPS`.

---

## 2026-05-21 — Этап 2: BaseVKChecker + удаление UnifiedNotificationsChecker

**Тема сессии:** архитектурная чистка модуля notifications по плану из PENDING_FOLLOWUPS этап 2. Цель — устранить дублирование fallback-логики между тремя checker'ами и убрать UnifiedNotificationsChecker (тот самый, где на строке 43 был скрытый баг — `suggested_checker` без `community_tokens`).

### Изменения

#### Новый `modules/notifications/base_checker.py`

- **`BaseVKChecker`** с общим `__init__` (создание session/vk/community_tokens), `_api_for(group_id)` с **lazy-cache** `_community_apis: {community_id → vk_api_handle}` (раньше каждый `_api_for` создавал новый `VkApi(token=...).get_api()` — на ~1000 калов за час лишний overhead) и `_call_with_fallback(group_id, op_name, fn, fallback_codes=COMMUNITY_FALLBACK_CODES)`.
- Константа модуля **`COMMUNITY_FALLBACK_CODES = frozenset({15, 27})`** — single source of truth для всех трёх checker'ов.

#### `vk_suggested_checker.py`, `vk_comments_checker.py`, `vk_messages_checker.py`

- Наследуются от `BaseVKChecker`. Удалены дублированные `__init__` / `_api_for` / `_COMMUNITY_FALLBACK_CODES` / локальные `_call_with_fallback`.
- `VKCommentsChecker.check_post_comments_since` использует общий `_call_with_fallback` из базового класса (раньше был локальный).
- `VKSuggestedChecker.check_suggested_posts` сократился втрое: вся логика fallback теперь в базе.
- `VKMessagesChecker` особо отмечен: для него не используется auto-fallback (специфика messages-сценария: code 15 у user-token означает «scope messages не выдан», там нет «retry», там denied_groups).

#### Удалён `modules/notifications/unified_checker.py` (226 строк)

Вместо `UnifiedNotificationsChecker.check_all()` обёртки `web/api/notifications.py:check_all_now` теперь напрямую инстанциирует `VKSuggestedChecker` и `VKMessagesChecker` и вызывает их методы. Это:
- Убирает скрытый баг (на строке 43 unified_checker'а `VKSuggestedChecker(vk_token)` создавался **без** `community_tokens` — этап 0 hot-fix этот путь не покрывал, потому что fallback стоял в самом checker'е, а Unified просто не пробрасывал tokens).
- Снижает индирекцию: 2 уровня вызова → 1.
- Делает Telegram-уведомление переиспользуемым: вынесено в `modules/notifications/telegram_alert.py::send_telegram_notifications_alert(...)`.

#### Удалены: `tasks/notification_tasks.py` (110 строк), `scripts/test_notifications_system.py` (140 строк)

`tasks/notification_tasks.py:check_vk_notifications` нигде не зарегистрирован в `beat_schedule` (`tasks/celery_app.py`) — мёртвый код, дубликат функциональности `check_suggested_posts` + `check_unread_messages`. Использовал `UnifiedChecker`. Удалён.

`scripts/test_notifications_system.py` — ad-hoc manual test script на UnifiedChecker, тоже устарел.

#### `tasks/celery_app.py`

- В `check_unread_messages` и `check_recent_comments` удалена **inside-task проверка часа** (`if not 8 <= current_hour < 22: return {'skipped': True...}`). Окно уже гарантировано `crontab(minute=N, hour='8-22')` в beat-расписании. Раньше двойная защита просто крала ресурсы worker'а на skipped-таски.

### Тесты

- **Новый `tests/test_notifications/test_base_checker.py`** — 7 тестов:
  - `_api_for` без community-token → user-api.
  - `_api_for` c community-token → корректный handle + **кеш-хит** на втором вызове (важно для прод-нагрузки).
  - `_call_with_fallback` happy path: community-token успешен.
  - retry на code 27 → второй вызов через user-api, метка `community-fallback-user`.
  - retry **не делается** на code 100 (вне `COMMUNITY_FALLBACK_CODES`).
  - retry **не делается** если community-token не настроен (нечего фолбэкить с).
  - константа `COMMUNITY_FALLBACK_CODES` содержит 15 и 27.
- Обновлены патч-таргеты в существующих тестах: `modules.notifications.{vk_*_checker}.vk_api.VkApi` → `modules.notifications.base_checker.vk_api.VkApi` (теперь `vk_api` импортируется только в base).
- **197/197 pytest green** (190 после hot-fix-2 + 7 новых).

### Применение

- Деплой через push → SSH `git pull` + restart.
- На проде после deploy: автотаски `check_*_hourly` пойдут через новые наследуемые checker'ы, fallback при error 27 уже работает (после hot-fix-2 propagation того же дня).

---

## 2026-05-21 — Hot-fix 2: VK error_code propagation для fallback wall.repost

**Тема сессии:** доделать этап 0 — на проде wall.repost всё ещё падал с error 27 несмотря на новый fallback. Причина: `VKClient.api_call` возвращает `{'error': {'error_msg': str(ApiError)}}` БЕЗ `error_code` ключа, а мой `_invoke` смотрел только `err.get('error_code')` (получал 0), не парсил из `error_msg`. Fallback-set `{15, 27}` не матчил, retry не вызывался.

### Изменения

- **`modules/vk_monitor/vk_client.py:api_call`** — теперь возвращает `{'error': {'error_code': int(e.code), 'error_msg': str(e)}}`. Чистое решение: всему коду, который консьюмирует VKClient ответы, теперь доступен `error_code`.
- **`modules/publisher/vk_publisher_extended.py:_invoke`** — добавлен парсинг `^[(\d+)]` regex из `error_msg` как fallback на случай legacy-формата (без `error_code`). Belt + suspenders, чтобы регрессии не сломали retry.
- **`tests/test_notifications/test_publisher_fallback.py::test_fallback_on_code_27_when_only_in_error_msg`** — регресс-тест на legacy-формат: error без `error_code`, только `error_msg`. Fallback должен сработать.

### Подтверждение на проде

- Логи 14:07 (до деплоя hot-fix-2): `❌ Failed to repost to group -158787639: VK API error: [27]` × 7 групп.
- Логи после деплоя 14:28 (этап 0 + 1 + hot-fix-2): следующий repost-тик в 14:37 — будет первым с активным fallback. Ожидаем 0 ошибок 27 во всех 7 группах.

---

## 2026-05-21 — Полный сбор комментариев: пагинация + thread.items + расширенные метаданные

**Тема сессии:** этап 1 рефактора уведомлений по плану из PENDING_FOLLOWUPS. Исправлены три проблемы сбора комментариев под постами региональных сообществ.

### Проблемы (которые жаловался пользователь — «комменты теряются»)

1. **`max_total_comments=300`** обрывал обход на первых ~3 постах с активной перепиской: «первые 3 поста дали 300 комментов → следующие 47 постов вообще не сканируются».
2. **`wall.getComments(count=100)` без пагинации**: пост с 250 комментами терял 150 хвостовых (с учётом `sort='desc'` — самые старые внутри 24ч окна).
3. **Ответы на комментарии (`thread.items`)** не распаковывались, полностью пропадали — а это часто половина диалога.

### Изменения

#### `modules/notifications/vk_comments_checker.py`

- **`check_post_comments_since` переработан** под пагинацию:
  - Цикл `offset += 100` пока (а) есть items, (б) хотя бы один коммент партии новее `cutoff_ts`, (в) `offset < total`, (г) не превышен safety-cap `_PAGES_PER_POST_LIMIT=50` (5000 комментов на пост).
  - `extended=1` + `thread_items=1` в API-запросе.
  - Каждый `thread.items[i]` плоско добавляется в результат с пометками `parent_id` и `is_reply=True`. Ответы старше cutoff фильтруются.
  - `sort='desc'`: новые сверху → останов на первой полностью out-of-window странице вместо дочитывания до конца.
- **`max_total_comments` поднят 300 → 5000** в `check_recent_comments_for_posts` и `check_recent_comments_for_region_groups`. Теперь это **safety-cap от raw-памяти**, а не обрыв обхода: все посты сканируются всегда; лимит срабатывает только на крайне-активной стене (виральный тред 5000+ комментов).
- **Расширенные метаданные** в каждой notification-записи: `parent_id`, `is_reply`, `from_id`, `likes_count`, `has_attachments`. Это даст UI на этапе 4 (mark-as-handled, виджет «Горячие посты»: «N без ответа», лайк коммента от имени сообщества).
- Выделены статические хелперы `_build_comment_notification` (map raw-comment → notification, фильтр empty-text), `_sort_newest_first`.

### Тесты

- **`tests/test_notifications/test_comments_pagination.py`** — 7 новых:
  - single page < 100 не пагинируется.
  - 3 страницы (250 комментариев), offsets 0/100/200.
  - выход из 24ч окна на 2-й странице → останов без 3-й.
  - `thread.items` плоско распакованы (3 элемента: parent + 2 reply).
  - `thread.items` старше cutoff не попадают.
  - termination когда `count` (total) исчерпан.
  - safety-cap: бесконечный поток страниц → ровно 5000 комментариев и warning.
- Прогон: 189/189 зелёные (182 после этапа 0 + 7 новых).

### Применение

- Деплой через `/reliz`-флоу: pytest → commit → push → SSH `git pull` + restart → проверка следующего `check_recent_comments` тика.
- Совместимость: параметр `max_comments_per_post=100` в публичных методах сохранён для back-compat, но реально не используется (пагинация по `_PAGE_SIZE=100`).

### Хвосты, оставленные в `PENDING_FOLLOWUPS.md`

- ⏳ Этап 2: BaseVKChecker (DRY для fallback/retry-логики) + удалить UnifiedNotificationsChecker.
- ⏳ Этап 3: история проверок в Redis-list для виджета «активность за сутки».
- ⏳ Этап 4a/b: UI feedback (inline-ответ, лайк, шаблоны, AI-черновик, mark-as-handled, hot posts).
- ⏳ Этап 5: Prometheus метрики + алёрт по token health.

---

## 2026-05-21 — Hot-fix VK community-tokens: fallback на user-token при error 15/27

**Тема сессии:** аудит модуля уведомлений по жалобе «автопроверка нестабильна, комментарии теряются». Обнаружено: с PR #9 от 19 мая все три checker'а и публикатор используют community access tokens приоритетно. Эти токены созданы без scope `manage`, поэтому VK возвращает code 27 «Group authorization failed» на `wall.get(filter='suggests')`, `wall.repost` и `wall.post`. Падали — тихо, без fallback. Эффект: автотаски `check_suggested_posts` каждый час писали в Redis `notifications: []`, стирая результат успешной ручной проверки; публикация дайджестов в 7+ регионах падала.

### Изменения

#### Fallback при VK API error 15/27

- **`modules/notifications/vk_suggested_checker.py`** — изолирован VK-вызов в `_wall_get_suggests(api, group_id)`. Главный метод `check_suggested_posts`: при `ApiError.code in {15, 27}` через community-token → автоматический повтор через `self.vk` (user-token). Результат теперь содержит поле `via` (`community-token` / `user-token` / `community-fallback-user`) для observability в логах.
- **`modules/notifications/vk_comments_checker.py`** — добавлен helper `_call_with_fallback(group_id, op_name, fn)` для общей логики «попробуй community → при 15/27 повтори через user». `check_post_comments_since` и `_get_recent_wall_posts_with_comments` теперь оба идут через него. **Заодно исправлен скрытый баг** — `_get_recent_wall_posts_with_comments` использовал `self.vk` напрямую, минуя `_api_for(owner_id)`; то есть для списка постов использовался user-token, а для комментов под ними — community-token (рассинхрон).
- **`modules/publisher/vk_publisher_extended.py`** — `_call_wall_post` разбит на `_call_wall_post` (со стратегией) + `_invoke` (одиночный вызов). При код 15/27 на community-client (не на `self.vk_client`) — повтор через publish-token. Новый internal exception `_VKApiCallError(code, message)` для надёжной диагностики VK-ответа.

#### Storage: автотаска не стирает ручной результат

- **`modules/notifications/storage.py`** — `save_notifications` получил параметры `keep_if_empty: bool = False` (по умолчанию — старое поведение) и `keep_window_hours: int = 6`. Если новый список пустой, существующий ключ непустой и моложе `keep_window_hours` — НЕ перезаписываем. Это убирает паттерн «ручная нашла 4, через час автотаска вернула 0 из-за временной VK-ошибки и стерла».
- **`tasks/celery_app.py`** — для `check_suggested_posts` и `check_recent_comments` передан `keep_if_empty=True`. Для `unread_messages` оставлено старое поведение (0 непрочитанных — легитимный результат, и есть отдельный `denied_groups` для индикации проблем с токеном).

### Тесты

- **Новый каталог `tests/test_notifications/`** с четырьмя файлами:
  - `test_suggested_fallback.py` (5 тестов): fallback на 27, на 15, отсутствие fallback без community-token, неоднозначные ошибки не ретраятся, happy path.
  - `test_comments_fallback.py` (4 теста): fallback на 27 в `wall.getComments`, **регресс-тест на баг с `self.vk` в `_get_recent_wall_posts_with_comments`**, fallback на `wall.get`, неоднозначные ошибки не ретраятся.
  - `test_publisher_fallback.py` (5 тестов): repost fallback на 27, post fallback на 15, отсутствие двойного retry если уже publish-client, неоднозначные ошибки propagate, happy path.
  - `test_storage_keep_if_empty.py` (6 тестов): сохранение непустого предыдущего, замена устаревшего, запись при отсутствии ключа, перезапись непустым, дефолтное поведение, обработка corrupt timestamp.
- Прогон: 182/182 зелёные (162 старых + 20 новых).

### Применение

- Деплой через `/reliz`-флоу: pytest → DEV_HISTORY/PENDING → commit → push → SSH `git pull` → restart `setka setka-celery-worker setka-celery-beat` → проверка `/api/health/full` + просмотр `celery-worker.log` следующего часового тика (ожидаем что worker.log перестанет показывать «VK API error code 27 for group X» в `check_suggested_posts`).

### Хвосты, оставленные в `PENDING_FOLLOWUPS.md`

- ⏳ Этап 1: полный сбор комментариев (убрать `max_total_comments=300`, добавить пагинацию по `offset`, распаковать `thread_items=1`).
- ⏳ Этап 2: BaseVKChecker + удалить `UnifiedNotificationsChecker` (в нём баг на строке 43 — `suggested_checker` создаётся без `community_tokens`, спасает только это; после удаления Unified логика идёт прямо).
- ⏳ Этап 3: storage с историей проверок (Redis-list `setka:notifications:history:{type}`, виджет «активность за 24ч»).
- ⏳ Этап 4 (UI feedback): inline-ответ из SETKA, mark-as-handled / архив, виджет «Горячие посты», AI-черновик через Groq, **лайк коммента от имени сообщества**, **шаблонные ответы на сообщения с отдельным редактором шаблонов**.
- ⏳ Этап 5: Prometheus метрика `notifications_check_total{type,result}`, алёрт «3 автопроверки подряд с error 27».

---

## 2026-05-21 — Inline-редактирование сообществ + AI-инструментарий (CLAUDE.md, slash-команды)

**Тема сессии:** доделать висевший с 12 мая WIP на `/communities` и принести в проект инфраструктуру для AI-сессий по образцу Гоньбы/MatricaRMZ.

### Изменения

#### `/communities` — inline edit имени + кнопка удаления (UI-фича)

- **`web/api/communities.py`** — в `CommunityUpdate` добавлено поле `name: Optional[str]`. В `update_community` починен баг: после `pop('region_id')` оставшиеся поля (`name`, `category`, `is_active`) **не применялись** к объекту. Теперь применяются через `setattr(community, field, value)` с пропуском `None` и trim для имени; пустое имя возвращает 400. DELETE-эндпоинт уже был на месте.
- **`web/templates/communities.html`** — имя сообщества в таблице теперь `<input onchange="onCommunityNameChange(...)">` вместо статичного `<strong>`, добавлена кнопка-корзина `deleteCommunity(...)`. Дописаны две JS-функции (по образцу `onCommunityCategoryChange`), которые ранее были вызваны но не определены — без них UI был ломанный.
- Тесты на API сообществ отсутствуют (`tests/test_api/` есть только `test_filtration.py`) — проверка вручную через UI после деплоя.

#### AI-инструментарий (по образцу Гоньбы/MatricaRMZ, адаптировано под Сетку)

- **`CLAUDE.md` в корне** — entry-point для AI-сессий: язык, источники правды, жизненный цикл задачи, правила (SSH-only прод, секреты, локальная разработка), conventional commits, типичные команды для troubleshooting.
- **`docs/PENDING_FOLLOWUPS.md`** — открытые задачи и техдолги с приоритетами 🔴⏳🟡🟢. Наполнено реальными пунктами Сетки (хардкод `app.log`-пути, ручные SQL-миграции, отсутствие `dev-doctor.sh`, идеи дашбордов и алёртов).
- **`docs/DEV_HISTORY.md`** — добавлен шаблон новой записи в шапку файла (в `<details>`-блоке), чтобы будущие сессии писали по единому формату.
- **`.claude/commands/`** — 7 slash-команд:
  - `/start` — git fetch, чтение SoT, проверка venv, опциональный SSH-probe (через `AskUserQuestion` из-за auto-mode classifier), отчёт «чем займёмся».
  - `/check` — health-таблица: pytest + prod systemd + curl health + Redis cooldown + ошибки worker.
  - `/celery` — состояние Celery (workers, beat, последние публикации, Redis-cooldown по регионам), фильтры `--errors` / `--region=<code>`. **Уникально для Сетки.**
  - `/logs` — параметризованный просмотр прод-логов (app/worker/beat/nginx/backup) с `--grep`, `--since`, `--errors`, `--journal`. Маскирует `access_token=***` в выводе.
  - `/sql` — psql на проде с `AskUserQuestion` для DML, шорткаты (`tables`, `describe`, `count`, `migrate <file>`).
  - `/reliz` — тесты → DEV_HISTORY+PENDING_FOLLOWUPS → commit → push → SSH `git pull` → миграции → restart → health-проверки → откат при провале.
  - `/finish` — мягкое закрытие сессии без деплоя: проверка DEV_HISTORY+PENDING, опциональный commit.
- **`.gitignore`** — добавлены `.claude/*` + исключения `!.claude/commands/` / `!.claude/agents/` (формат Гоньбы), чтобы `worktrees/` и локальные settings никогда не попадали в коммит.

### Проверка / прогон

- Локально: правки бэка тривиальные, тесты `pytest tests/ -q` будут прогнаны через `/reliz`.
- UI: проверять руками после деплоя через прод-страницу `/communities` (превью в Launch panel показывает шаблон).
- SQL-миграции в этой сессии не было. Миграция `007_vk_tokens_community_id.sql` от 2026-05-19 (PR #8) — будет применена на проде в этом же релизе.

### Применение

- Синхронизация: локалка отставала от `origin/main` на 6 коммитов (PR #1, #3, #5, #6, #8, #9) — подтянуто через `git pull --ff-only` (через stash → pull → unstash, конфликт в `DEV_HISTORY.md` разрешён сохранением порядка «свежее сверху»).
- Деплой через SSH `setka-prod` → `git pull --ff-only` + миграция `007` + `systemctl restart setka setka-celery-worker setka-celery-beat`.

### Хвосты, оставленные в `PENDING_FOLLOWUPS.md`

- 🟡 `main.py:25` хардкодит путь к логам — мешает локальному запуску приложения.
- 🟡 Нет скрипта `scripts/setup-dev.ps1`/`.sh` — venv создаётся вручную.
- 🟡 SQL-миграции применяются ручным `psql -f`, нет учёта `applied_migrations`.
- 🟡 Auto-mode classifier требует подтверждения на каждую SSH-команду — стоит решить permission rule.
- 🟢 Скрипт `dev-doctor`, hook на commit с напоминанием про DEV_HISTORY, smoke-test после деплоя.
- 🟢 Grafana-панель состояния дайджестов, алёрт «6 часов без `novost`», структурированные логи.
- 🟢 Продуктовые: UI «История публикаций», тёмный режим, кнопка «прогнать пайплайн без публикации» в UI региона.

---

## 2026-05-19 — Community-токены приоритет: publisher, suggested, comments, messages, copy_setka

- **Идея пользователя:** если у нас уже есть community access token с полными правами (Управление + Сообщения + Стена + Фото + Документы + Истории + Товары), эти токены логично использовать **для всех операций над своими группами**: публикация дайджестов, проверка предложек/комментариев/сообщений, копи-сетка. Это снимает нагрузку с VALSTAN/VITA, разносит rate-limit по 14 отдельным пулам (а не 2) и снижает риск VK-бана за «нерациональное использование API».
- **Хелпер:** новый `modules/vk_token_router.py` с `load_community_tokens(session) -> Dict[int, str]` (один select по `vk_tokens` where `community_id IS NOT NULL AND is_active`) и `pick_token(...)`. Используется во всех точках, где раньше тянулся VK_TOKEN_VALSTAN.
- **VKPublisher** (`modules/publisher/vk_publisher_extended.py`):
  - `__init__(..., community_tokens={cid: token})`; ленивый кэш `_community_clients`.
  - `_client_for_group(target_group_id)` возвращает `(client, via_community)`: community-VKClient если есть, иначе общий publish-VKClient.
  - `publish_digest` и `publish_repost` используют клиент через `_client_for_group(target)`. В логах теперь `via=community-token` / `publish-token`.
  - `_call_wall_post(params, method, client=...)` принимает явный клиент, по умолчанию `self.vk_client`.
- **VKSuggestedChecker** (`modules/notifications/vk_suggested_checker.py`) и **VKCommentsChecker** (`modules/notifications/vk_comments_checker.py`): добавлены `community_tokens` в `__init__` и `_api_for(group_id)` helper. `wall.get` / `wall.getComments` теперь идут под community-токеном целевой группы, если такой есть.
- **Pre-loading при вызове:** `tasks/celery_app.check_suggested_posts/check_unread_messages/check_recent_comments`, `tasks/parsing_scheduler_tasks.parse_and_publish_theme`, `modules/kirov_oblast_digest.run_kirov_oblast_digest`, `modules/copy_setka_network.execute_copy_setka_network` — все вызывают `load_community_tokens(session)` (1 SELECT/прогон) и передают в нужный класс.
- **Парсинг (отложено):** основной парсер `AdvancedVKParser` читает **чужие** новостные сообщества (источники для дайджестов) — там community-токенов нет в принципе, user-token (VALSTAN/VITA) — единственный путь. Чтение своих стен из oblast-digest потенциально могло бы использовать community-токены, но требует расширения VKClient/parser; вынес в follow-up.
- **Поведение fallback:** если community-токена нет — всё работает как раньше через VALSTAN. Никакой регрессии: ни один существующий путь не сломан, просто приоритет переехал на community-токены, когда они есть.
- Тесты: 162/162 unchanged. Прода после merge ничего не требует — миграция уже применена, токены будут подхватываться автоматически при следующем `check_unread_messages` / следующей публикации.

---

## 2026-05-19 — Community access tokens для чтения сообщений сообществ

- **Контекст:** VK ограничивает scope `messages` для user-токенов (выдаётся только апп-ам с whitelist'ом). VK Admin / Postopus / Kate Mobile / iPhone — ни один из них в проде не сработал. Альтернативный путь, который рекомендует сам VK, — community access tokens: токен выдаётся в `vk.com/club{ID}` → Управление → Работа с API → Создать ключ, с правом «Сообщения сообщества». Такой токен умеет звать `messages.getConversations` без `group_id`-параметра, без user-токена со scope `messages`.
- **Миграция БД:** `database/migrations/007_vk_tokens_community_id.sql` добавляет nullable `community_id BIGINT` + partial index `idx_vk_tokens_community_id`. Значение хранится как `abs(group_id)` для лёгкого джойна с `regions.vk_group_id` (там встречаются и положительные, и отрицательные ID).
- **Модель** `database/models.VKToken`: добавил `community_id` поле + в `to_dict()`. `repr` стал чуть полезнее.
- **API** `web/api/token_management.py` — новый блок ниже `GET /` и **выше** `GET /{token_name}` (порядок критичен, иначе FastAPI ловит `/communities` как `token_name="communities"`):
  - `GET  /api/tokens/communities` — per-region список со статусом community-токена (regions JOIN vk_tokens по community_id);
  - `PUT  /api/tokens/communities/{community_id}` — upsert токена с автоматической валидацией;
  - `POST /api/tokens/communities/{community_id}/validate` — пере-проверить;
  - `DELETE /api/tokens/communities/{community_id}` — снять токен.

  Валидация выделена в отдельный `validate_community_token(token, community_id)` — community-токен **не** проходит `users.get` (у него нет пользователя), валидность проверяется через `groups.getById(group_id=cid)` + `messages.getConversations(count=1)`. Также `add_token`/`update_token`/`validate_token`/`validate_all_tokens` теперь выбирают между `validate_single_token` и `validate_community_token` по наличию `community_id` у записи.
- **Checker:** `VKMessagesChecker.__init__(vk_token, community_tokens={cid: token})`. Внутри помощник `_api_for(group_id)` возвращает `(vk_api, via_community)`: если для группы есть community-токен — звонок идёт под ним и без `group_id`-параметра, иначе — fallback на user-токен. Лог `Group X: N unread messages (via=community-token|user-token)`.
- **Pre-loading:** `tasks/celery_app.check_unread_messages` и `web/api/notifications.check_all_now` теперь делают `SELECT FROM vk_tokens WHERE community_id IS NOT NULL AND is_active` и передают в `UnifiedNotificationsChecker(vk_token, community_tokens=...)`. Один запрос на прогон, ноль изменений в горячем пути.
- **UI** (`web/templates/tokens.html`): на странице `/tokens` под существующей сеткой токенов появилась карточка «Токены сообществ — чтение сообщений» с per-region таблицей (Регион / VK group / поле для access_token / статус / кнопки 💾 ✅ 🗑️). Заголовок объясняет где взять токен: `vk.com/club{ID}` → Управление → Работа с API → Создать ключ → «Сообщения сообщества». JS: `refreshCommunityTokens`/`saveCommunityToken`/`validateCommunityTokenRow`/`deleteCommunityToken`. Ссылка на `https://vk.com/club{ID}/managers` идёт прямо в раздел управления для удобства.
- **Why это работает там, где user-token упирался в `[15]`:** community-токен живёт «внутри» группы — VK не делает security-review приложения, потому что приложение тут не у дел; права выдаёт админ группы (то есть пользователь сам себе) через интерфейс VK. Поэтому `messages.read` идёт по дефолту.
- Тесты: 162/162 unchanged. Миграцию `007` нужно прогнать на проде: `psql $DATABASE_URL -f database/migrations/007_vk_tokens_community_id.sql`.

---

## 2026-05-19 — Уведомления VK: диагностика «нет доступа» вместо «всё проверено»

- **Симптом:** на странице «Уведомления VK» блок «Непрочитанные сообщения» стабильно показывал «Нет непрочитанных сообщений. Все проверено!», хотя сообщения сообществам приходят. В логах рядом — `WARNING modules.notifications.vk_messages_checker - No access to messages for group -...` × 14 (по числу регионов), повторяется каждый час; 658 строк access denied за последние сутки.
- **Корень:** VK API `messages.getConversations(group_id=...)` с user-токеном требует scope `messages` И прав админа сообщества. Probe на проде показал:
  - `messages.getConversations(group_id=X)` → `[15] Access denied: no access to call this method`
  - `messages.getConversations(count=1)` без group_id → тот же 15 (т.е. у токена вообще нет scope `messages`)
  - `groups.getCallbackServers(group_id=X)` → OK (scope `manage` присутствует)
  - `groups.getById` → `is_admin=1, admin_level=3` (бот реально админ)

  Свежий VALSTAN-токен после сегодняшней ротации был сгенерирован без scope `messages` — VK Admin app (`client_id=2685278`) не выдаёт этот scope. Нужен либо собственный app (`postopus`, id 51421557, в `apps.get`), либо app с whitelisted-scope, и явное `&scope=…,messages` при OAuth.
- **Дополнительный баг — `validate_single_token` врал:** `web/api/token_management.py::validate_single_token` собирал permissions через `try: await get_X(); permissions.append('X')`. Но `VKClient.get_messages/get_posts/get_groups` глотают `ApiError` и возвращают `None` — exception не поднимается, и `messages.read` добавлялся всегда. UI токенов показывал `["wall.read", "groups.read", "messages.read"]` при фактическом отсутствии scope. Поправил на `if await get_X(...) is not None: permissions.append('X')`.
- **Диагностика в UI:** `modules/notifications/vk_messages_checker.py::check_all_region_groups` теперь возвращает `{notifications, denied_groups}` вместо плоского списка. `unified_checker`, `tasks/celery_app.check_unread_messages`, `NotificationsStorage` (новый Redis-ключ `setka:notifications:unread_messages_denied`) и `web/api/notifications.py` пробрасывают `messages_denied_count` + `unread_messages_denied`. Front-end (`web/templates/notifications.html` + `web/static/js/notifications.js`): когда `denied_groups.length > 0` появляется жёлтый alert «Нет доступа к сообществам VK. Выпустите токен с scope `messages,manage,groups,offline`» с перечнем затронутых групп. Зелёный «Всё проверено» теперь показывается **только** при пустом denied И пустом unread.
- **Что должен сделать пользователь:** перевыпустить VALSTAN-токен в OAuth implicit flow с явными `scope=offline,wall,groups,photos,docs,messages,manage` через собственный app (или другой app, у которого whitelisted `messages` scope), записать в `/etc/setka/setka.env::VK_TOKEN_VALSTAN`, рестартнуть сервисы. После этого баннер «нет доступа» исчезнет, в списке появятся реальные непрочитанные.
- Тесты: 162/162 unchanged.

---

## 2026-05-19 — UI: dropdown-меню + footer + single-source версия (1.5.0)

- **Симптом:** на узких экранах верхнее меню (11 кнопок) не помещалось по ширине и переносилось. В подвале — статичный `SETKA v1.0 | © 2025 Valstan` ещё с октября.
- **Меню:** `web/templates/base.html` переработан в 3 dropdown'а Bootstrap 5 + Dashboard:
  - **Dashboard** (одиночная кнопка) — `/`
  - **Контент ▾** — Регионы, Сообщества, Посты, Фильтрация
  - **Пайплайн ▾** — Парсинг, Расписание, Статистика
  - **Система ▾** — Мониторинг, Уведомления, Токены
  - справа: `API` (ведёт на FastAPI `/docs`), индикатор статуса.

  Итого 4 видимых пункта вместо 11 + иконки `bootstrap-icons` для каждого. К каждому пункту добавлены классы `dropdown-toggle`/`dropdown-menu`/`dropdown-item` стандартного Bootstrap 5 — JS уже подключён через `bootstrap.bundle.min.js`.
- **Active-highlight:** введены 3 новых блока `nav_section_content`, `nav_section_pipeline`, `nav_section_system`. В каждом дочернем шаблоне (regions/communities/posts/filtration/parsing/schedule/parsing_stats/monitoring/notifications/tokens) рядом с существующим `{% block nav_X %}active{% endblock %}` добавлен соответствующий `{% block nav_section_Y %}active{% endblock %}` — при заходе на страницу подсвечивается и сам пункт в выпадашке, и триггер группы. Заодно у `parsing_stats.html` починена давно отсутствовавшая `nav_parsing_stats` подсветка (раньше там просто не было блока).
- **Footer:** `SETKA {{ app_version }} | Production | © 2025–<год> Valstan`, где `app_version` подставляется как Jinja global, а год — динамически из JS (`new Date().getFullYear()`). API Docs ссылка исправлена с `/api/docs` на `/docs` (FastAPI монтирует Swagger именно туда).
- **Версия:** добавлен `_version.py` в корне репо с `__version__ = "1.5.0"`. В `main.py` теперь `from _version import __version__ as APP_VERSION` → используется в `FastAPI(version=...)` и регистрируется как `templates.env.globals["app_version"]`. Раньше в коде висел захардкоженный `version="1.0.0"`.
- **Почему 1.5.0:** реконструировал по git-милстоунам (см. docstring `_version.py`):
  - 1.0.0 — initial 2025-10-09
  - 1.1.0 — Postopus migration 2026-04-08
  - 1.2.0 — digest formatting + token roles + mourning split 2026-04-13
  - 1.3.0 — copy_setka + Filtration UI + Kirov oblast 2026-04-20
  - 1.4.x — region filter morphology + dedup hardening + log-noise/metrics/empty-digest фиксы (май 2026)
  - 1.5.0 — UI: grouped navigation + dynamic footer (этот PR)

---

## 2026-05-19 — Запрет публикации пустых дайджестов (только заголовок + хештеги)

- **Симптом:** в ленту сообщества прилетал дайджест вида:
  ```
  Физическое развитие:

  #спортМалмыж #малмыж
  ```
  то есть только заголовок и хештеги, без единого поста-источника.
- **Причина:** `DigestBuilder.build_digest` в `modules/publisher/digest_builder.py` сначала добавлял заголовок в `digest_parts`, потом проходил по постам с `continue`-ветками (пустой `post_text.strip()`, не влазит в `max_text_length`, не хватает слотов под `attachments`). Если **все** кандидаты пропускались, цикл просто заканчивался, к `digest_parts` приклеивались хештеги и возвращался `DigestResult(text="<header>\n\n#tags", post_count=0, ...)`. Caller'ы (`tasks/parsing_scheduler_tasks.py`, `modules/kirov_oblast_digest.py`) проверяли только `if regular_posts:` (есть ли кандидаты на входе), но не финальный `post_count`, и публиковали такую заглушку через `vk.wall.post`.
- **Решение (3 слоя):**
  - В `DigestBuilder.build_digest`: если после цикла `posts_included` пуст — возвращаем полностью пустой `DigestResult(text="", attachments_list=[], post_count=0, ...)`. Никаких header/hashtags в одиночку.
  - В обоих production-callers (`tasks/parsing_scheduler_tasks.py` для regular+mourning, `modules/kirov_oblast_digest.py` для regular+mourning) перед `vk_publisher.publish_digest(...)` добавлена проверка `if digest.post_count == 0 or not digest.text.strip(): logger.warning(...)` и `else: publish`. В oblast также инкрементируется `debug_counters["filtered_posts_empty_digest"]`.
- Тесты: 3 новых в `tests/test_publisher/test_digest_builder.py` — `test_all_posts_empty_text_yields_empty_digest` (whitespace-only тексты), `test_no_posts_fit_yields_empty_digest` (`max_text_length` слишком жадный), `test_at_least_one_post_fits_produces_normal_digest` (sanity). Полный прогон 162/162 зелёные.

---

## 2026-05-19 — Фикс /metrics: asyncio.run внутри event loop и обновление messages.get

- `monitoring/metrics.py`: `get_cache_metrics()` дёргал `asyncio.run(cache.get_stats())`, что валилось на `RuntimeError: asyncio.run() cannot be called from a running event loop` при обращении к `/metrics` (FastAPI-эндпоинт уже под loop). Перевёл `get_cache_metrics` и `get_metrics` в `async`, обновил вызов в `main.py:226` на `await get_metrics()`. Симптом в `logs/app.log` 2026-05-14: `Failed to get cache metrics: asyncio.run() cannot be called from a running event loop`.
- `modules/vk_monitor/vk_client.py`: `get_messages()` вызывал `self.vk.messages.get(count=...)`, но `messages.get` удалён из VK API в 2016 году — отсюда `[3] Unknown method passed` в логах 2026-05-19 00:10 при `POST /api/tokens/{name}/validate` (там же проверяется permission `messages.read`). Переключил на `messages.getConversations(count=...)` — современный аналог, который успешно возвращает данные при наличии scope `messages` и фейлится с `Access denied` при его отсутствии. Заодно завернул VK ApiError в `_log_vk_api_error`, чтобы шум от тестов прав не валил уровень ERROR.

---

## 2026-05-19 — Уборка untracked-файлов, тише логи VK, фикс KeyError 'domain', обход блокировки Telegram

- На проде в `/home/valstan/SETKA` оставались 8 untracked-файлов (после ручной отладки): 7 root-скриптов (`check_region_config.py`, `check_vk_token.py`, `deep_vk_token_check.py`, `test_new_valstan_token.py`, `test_parse_run.py`, `test_production_pipeline.py`, `trigger_celery_task.py`) и orphan-модуль `modules/publisher/vk_client.py`. 5 root-копий были byte-identical с уже закоммиченными `scripts/<same>.py`, 2 (`test_parse_run.py`, `test_production_pipeline.py`) — устаревшие версии. `modules/publisher/vk_client.py` нигде не импортируется (используется `modules/publisher/vk_publisher_extended.py` и `modules/vk_monitor/vk_client.py`). Все 8 удалены, `git status` чист.
- `modules/vk_monitor/vk_client.py`: VK API ошибки с кодами `{15, 18, 203, 212, 220}` (доступ закрыт, пользователь удалён/забанен, нет доступа к группе и т.п.) перевели с `logger.error` на `logger.warning` через помощник `_log_vk_api_error`. Раньше за один прогон парсинга в лог летело по 30+ строк `ERROR ... [15] Access denied: wall is disabled`, что забивало мониторинг и метрики ошибок — это штатная ситуация для сообществ с закрытой стеной, пайплайн её корректно скипает.
- `web/api/notifications.py`: `dashboard_url = f"https://{SERVER['domain']}/notifications"` падал с `KeyError: 'domain'` — в `config/runtime.py` `SERVER` имел только `host`/`port`. Добавил ключ `domain` (env `SERVER_DOMAIN`, дефолт — текущий домен Jino), а в API использую `.get('domain')` с fallback на `host:port`. Endpoint `POST /api/notifications/check-now` перестал отдавать 500.
- `scripts/test_new_valstan_token.py`: убрал заинлайненный VK-токен в plaintext (был закоммичен в git с апреля), переписал на чтение из `config.runtime.VK_TOKENS["VALSTAN"]` + аргументы `--token-name/--owner-id`, дефолт `owner_id = VK_TEST_GROUP_ID`. **Токен по адресу `vk1.a.zhWLKN...` считать скомпрометированным — необходимо ротировать в VK** (Settings → Apps → Revoke).
- Прод: api.telegram.org с jino-VPS режется TLS-инспекцией на дефолтном IP `149.154.166.110` (TCP timeout 7с при ICMP/DNS ok), но `149.154.167.220` работает. Прописал `/etc/hosts: 149.154.167.220 api.telegram.org` — `curl https://api.telegram.org/` теперь отдаёт 302 за 0.2с, `check_recent_comments` сможет толкать алерты обратно в чат. Это stopgap, не код-фикс: если Telegram сменит IP, поправить вручную. Полноценное решение — env `TELEGRAM_API_BASE_URL` либо socks/http-прокси, но пайплайн алертов критичен и /etc/hosts достаточно надёжно.
- Тесты: полный прогон 159/159 зелёные после правок (vk_client.py, config/runtime.py, web/api/notifications.py).

---

## 2026-05-18 — RegionalRelevanceFilter подключён к RegionConfig + морфология + UI

- `modules/filters/regional.py`: фильтр перестал быть фактическим no-op в production-пайплайне. Раньше он ждал `region_id` в контексте, а `scripts/run_production_workflow.py` клал `region` (объект) — фильтр всегда возвращал `passed=True`. Теперь поддерживает оба варианта (через `_resolve_region`).
- Ключевые слова региона загружаются из `RegionConfig.region_words` (исторически `kirov_words` + `tatar_words` из MongoDB) и опционального нового поля `RegionConfig.localities` (JSONB — населённые пункты района). Базовые ключи из `Region.name`/`Region.code` остаются как fallback, мусорные токены (`ИНФО`, `НОВОСТИ`, `РАЙОН`, ...) отфильтровываются.
- Добавлена утилита `modules/filters/morphology.py` без сторонних либ: `get_word_stem`, `expand_keywords`, `text_matches_keyword`, `find_matching_keywords`. Срезает адъективные (`-ский/-ская/-ское`, `-ического`) и падежные окончания так, чтобы keyword «Малмыжский» матчил пост «В Малмыже прошёл фестиваль» и наоборот. Матчинг — по началу токена (через `re.findall`), без ложных подстрочных совпадений внутри слова.
- Дедупликация ключевых слов по lowercase (`«МАЛМЫЖ»` + `«Малмыж»` теперь один токен), TTL-кеш по `region_id` (5 минут, метод `invalidate_cache`).
- Миграция `database/migrations/006_region_configs_localities.sql` добавляет колонку `region_configs.localities JSONB DEFAULT NULL`. Применить на проде: `psql $DATABASE_URL -f database/migrations/006_region_configs_localities.sql`.
- UI «Фильтрация» (`web/templates/filtration.html`): новый textarea «Населённые пункты района» во вкладке «Списки и лимиты». В `web/api/filtration.py` добавлено поле `localities` в `FiltrationPutBody`, GET/PUT-роуты, выделена функция `_normalize_localities` (trim + дедуп без учёта регистра/ё). Заодно поправлен JS-баг: в save-body передавался shorthand `repost_words_blacklist`, тогда как переменная называлась `repost_words` (теперь явное `repost_words_blacklist: repost_words`).
- Тесты: 21 на морфологию + 12 на фильтр + 10 на API фильтрации (`tests/test_api/test_filtration.py`). Полный прогон проекта — 159/159 зелёные.

---

## 2026-05-12 — Исправление частоты дайджестов: catchup=False и лимит 1 дайджест/час на регион

- В `tasks/celery_app.py` добавлен `catchup=False` во все расписания Celery Beat, чтобы после простоев не догонялись пропущенные запуски (monitoring-hourly, check-suggested-hourly, check-unread-messages-hourly, check-recent-comments-hourly, digest-daily, cleanup-daily).
- В `modules/correct_workflow.py` добавлена проверка перед публикацией дайджеста: если в текущем часу уже был опубликован дайджест для региона, публикация пропускается (используется Redis-ключ `setka:digest_last_published:{region_id}:{hour}` с TTL 1 час).
- Это предотвращает слишком частые дайджесты после вынужденных простоев системы и ограничивает публикацию не чаще одного дайджеста в час в главном региональном сообществе.

---

## 2026-04-23 — Kirov oblast: восстановление публикации и жёсткая news-фильтрация

- В `modules/kirov_oblast_digest.py` добавлен явный отбор постов-источников только за 72 часа (`OBLAST_LOOKBACK_HOURS`) и только из дайджестоподобных записей с `wall`-ссылками; исключаются очевидные non-news digest-маркеры (`реклама/объявления/дополнительно/addons`) ещё на этапе извлечения ссылок.
- Для `kirov_obl/oblast` включены жёсткие исключения контента после общего пайплайна: реклама/addons, религиозные новости (словарь маркеров), mourning-посты (полностью исключаются из публикации областного дайджеста).
- Добавлена расширенная наблюдаемость запуска: `debug` counters в результате `run_kirov_oblast_digest()` (скан источников, отбор ссылок, отфильтрованные причины, готовые regular/mourning и т.д.).
- «Пустые» сценарии (`нет источников`, `нет ссылок`, `wall.getById вернул пусто`) переведены в мягкий `success=True` с диагностическим `message`, чтобы не маскироваться как аварийные сбои при штатном отсутствии кандидатов.
- Добавлены unit-тесты в `tests/test_kirov_oblast_digest.py` на 72-часовой отбор, digest-source pre-filter и религиозные маркеры.

---

## 2026-04-21 — Антидубли: общий LIP по региону и история целевой группы

- Для каждого региона введён общий контур дедупликации через `work_tables` с технической темой `__region_global__`: `lip/hash` теперь накапливаются не только в тематической таблице, но и в общем региональном контуре.
- При каждом парсинге формируется единый входной `work_table_lip/hash` как объединение **всех** `work_tables` региона, что блокирует повторное попадание одного и того же источника между разными тематиками.
- Добавлен дополнительный фильтр по фактически опубликованной ленте региона: парсятся последние 100 постов целевой группы и из текста дайджестов извлекаются `wall-...` ссылки источников; их LIP тоже попадает в дедуп до сборки нового выпуска.
- Вынесены утилиты `modules/deduplication/digest_history.py` (`build_region_dedup_sets`, `extract_source_lips_from_target_group_posts`, `append_unique_limited`) и добавлены тесты `tests/test_deduplication/test_digest_history.py`.

---

## 2026-04-21 — Дедуп дайджестов: усиление LIP и 90% похожесть текста

- В `modules/vk_monitor/advanced_parser.py` усилен конвейер дедупликации: кроме `lip`/точных hash, добавлена near-duplicate проверка текста на базе 64-bit SimHash с порогом похожести (по умолчанию `0.90`) и ограничением по длине нормализованного текста.
- Персистентный текстовый дедуп теперь использует `work_table.hash` с префиксами `txtfp:`, `txtcore:`, `txtsim:<bucket>:<hash>`; при парсинге эти сигнатуры учитываются вместе с батч-дедупом.
- После публикации в `tasks/parsing_scheduler_tasks.py` и `modules/kirov_oblast_digest.py` в `work_table` сохраняются не только `lip`, но и сигнатуры текста/медиа для будущих прогонов.
- Увеличены окна памяти дедупа в `work_table`: `lip` до 1000, `hash` до 5000 записей (вместо слишком короткой истории, из-за которой старые дубли возвращались).
- Расширены настройки `digest_filters` (`text_similarity_threshold`, `min_rafinad_len_similarity_dedup`) и покрытие тестами для SimHash/исторического дедупа.

---

## 2026-04-21 — Mourning-дайджесты: без любых заголовков и хештегов

- Для mourning-публикаций добавлен единый форматтер `resolve_mourning_digest_format()` в `modules/publisher/postopus_digest_headers.py`, который всегда возвращает пустые `header`, `hashtags` и `local_hashtag`.
- Production-пайплайны `tasks/parsing_scheduler_tasks.py` и `modules/kirov_oblast_digest.py` переведены на этот форматтер, чтобы траурные дайджесты по всем регионам/темам выходили без автоподстановок.
- Обновлены тестовые/диагностические скрипты (`scripts/test_production_pipeline.py`, `scripts/test_parse_run.py`) и убран декоративный префикс `🕯` из вывода тестового скрипта.
- Добавлены тесты `tests/test_publisher/test_postopus_digest_headers.py` и `tests/test_publisher/test_digest_builder.py` на поведение mourning-формата без заголовка и тегов.

---

## 2026-04-21 — Документация: только SSH для прода, без MCP

- Политика доступа: для SETKA **не использовать MCP** в IDE — только **стандартный SSH** на хост с `/home/valstan/SETKA` (обновлён [`REMOTE_ACCESS.md`](REMOTE_ACCESS.md)).
- Удалён [`MCP_SETUP_VSCODE.md`](MCP_SETUP_VSCODE.md); ссылки убраны из [`docs/README.md`](README.md), [`START_HERE.md`](START_HERE.md), [`AI_DEV_GUIDE.md`](AI_DEV_GUIDE.md).

---

## 2026-04-21 — UI «Фильтрация»: возраст постов по темам и правила RegionConfig

- Новая страница `/filtration` (меню **Фильтрация**): настройка `digest_filters` в `region_configs` (defaults + `by_topic`: `max_post_age_hours`, `max_posts_per_digest`, `min_rafinad_len_core_dedup`, `posts_per_community_fetch`), плюс редактирование `black_id`, `delete_msg_blacklist`, `time_old_post`, лимит длины поста, `setka_regim_repost`, `filter_group_by_region_words`, `repost_words_blacklist`.
- API: `/api/filtration/meta`, `/regions`, `GET/PUT /api/filtration/{region_code}`.
- Колонка БД: `region_configs.digest_filters` (JSONB) — миграция `database/migrations/005_region_configs_digest_filters.sql`.
- Парсер и `DigestBuilder` читают эффективные значения через `modules/digest_pipeline_settings.py` (`get_effective_pipeline_settings`).

---

## 2026-04-21 — Парсер дайджестов: дедуп репостов и текста

- **Проблема:** проверка `lip` шла до `clear_copy_history`, поэтому один оригинал, репощенный в разные группы, давал разные id и проходил несколько раз; `work_table_hash` / text dedup в `_filter_post` не использовались.
- **Исправление:** сначала unwrap репоста, затем `lip` против `work_table` и накопленного батча одного прогона; дедуп по `create_text_fingerprint` / `create_text_core_fingerprint` (rafinad ≥ 50) и по сигнатуре вложений `create_media_fingerprint`; пересечение с `work_hash_set` для известных id фото/видео.

---

## 2026-04-21 — Дайджесты: только посты не старше 72 часов

- В `AdvancedVKParser._filter_post` после разворачивания репоста проверяется поле `date` (Unix): если возраст публикации **> 72 ч**, пост отбрасывается (`posts_filtered_old`). Без даты — тоже отброс.
- Константа `DIGEST_MAX_POST_AGE_HOURS = 72` в `modules/vk_monitor/advanced_parser.py`.

---

## 2026-04-20 — Дайджесты: без заголовка «Скорбим», заголовки/хештеги как в Postopus, ссылки [url|название]

- Траурный дайджест: без строки-заголовка; внизу те же хештеги темы и региона, что и у обычного дайджеста этого запуска.
- Заголовок и хештеги: приоритет `RegionConfig.zagolovki` / `heshteg` (данные из Mongo/old_postopus); иначе fallback в `modules/publisher/postopus_digest_headers.py` (в т.ч. «Спортивные новости {регион}:»).
- Источник под постом: ВК-разметка `[https://vk.com/wall…|Название сообщества]`; имена подставляются из `communities` по `group_names`.
- Скрипт миграции: для Лебяжья спорт-заголовок приведён к «Спортивные новости Лебяжье:», исправлена опечатка ключа `reklama` у Советска.

---

## 2026-04-20 — Документация: SSH для удалённого доступа

- Добавлен [`REMOTE_ACCESS.md`](REMOTE_ACCESS.md): единое правило — работа с продом SETKA через SSH на хост с `/home/valstan/SETKA`.
- Обновлены [`START_HERE.md`](START_HERE.md), [`README.md`](README.md), [`AI_DEV_GUIDE.md`](AI_DEV_GUIDE.md). (Позже MCP убран из политики — см. запись 2026-04-21 выше.)

---

## 2026-04-20 — Фикс публикации дайджестов: нормализация group_id + fallback сообществ

### Проблема
- Дайджесты собирались, но публикация в часть регионов срывалась: `vk_group_id` в БД мог быть положительным (после миграций), а `wall.post` для групп требует `owner_id < 0`.
- В ряде регионов для конкретной темы не находились сообщества, из-за чего задача завершалась без попытки парсинга/публикации.

### Решение
- В `modules/publisher/vk_publisher_extended.py` добавлена нормализация ID группы: любые входные `group_id` приводятся к формату owner_id группы (`-abs(group_id)`) для `wall.post` и `wall.repost`.
- В `tasks/parsing_scheduler_tasks.py` добавлен fallback: если нет активных сообществ по `theme`, задача берёт все активные сообщества региона вместо мгновенного отказа.

### Проверка
- Добавлены unit-тесты `tests/test_publisher/test_vk_publisher_extended.py`:
  - нормализация positive/negative ID;
  - проверка `owner_id` для `publish_digest`;
  - проверка `group_id`/`object` для `publish_repost`.
- Локально: `pytest tests/test_publisher/test_vk_publisher_extended.py -q` → **3 passed**.

---

## 2026-04-20 — Scheduler: запуск только для валидных регионов

### Проблема
- `run_all_regions_theme` ставил задачи на все активные регионы, включая регионы без `RegionConfig`, без `vk_group_id` или без активных сообществ.
- Это давало «шумные» прогоны с быстрыми отказами и мешало диагностике реальных публикаций.

### Решение
- В `tasks/parsing_scheduler_tasks.py` ужесточён отбор регионов в `run_all_regions_theme(theme)`:
  - регион активен;
  - есть `vk_group_id`;
  - существует `RegionConfig` по `region_code`;
  - есть активные сообщества (по теме или хотя бы любые активные в регионе).

### Проверка
- Добавлен тест `tests/test_scheduler/test_parsing_scheduler_tasks.py` на постановку задач только по отобранным регионам.
- Регрессия publisher-тестов сохранена.
- Локально: `pytest tests/test_scheduler/test_parsing_scheduler_tasks.py tests/test_publisher/test_vk_publisher_extended.py -q` → **4 passed**.

---

## 2026-04-16 — Copy-by-setka: слово «репост», 10 постов / 10 lip, источник по умолчанию

- Источник по умолчанию: группа [copy_by_setka](https://vk.com/copy_by_setka), ID **-167381590** (переопределяется `COPY_SETKA_SOURCE_GROUP_ID`).
- За один запуск — **один** новый пост; `wall.get` не более **10** последних; в `lip` хранится не больше **10** идентификаторов.
- Если в поле `text` есть **«репост»** — `wall.repost` цели из `copy_history` или вложения `wall`; иначе — копия текста и вложений (с разворачиванием `copy_history` через `clear_copy_history`).
- `COPY_SETKA_DISABLED=1` — полностью отключить хаб.

---

## 2026-04-16 — Сетевой хаб `copy` / `setka` + пул БД + wall.repost

### Задача
- Расписание `postopus-copy-setka-07/37` вызывало `parse_and_publish_theme(copy, setka)`, но в БД не было `RegionConfig` для псевдо-региона `copy` — задача сразу выходила с ошибкой.
- Нужно: раз в ~30 мин читать **одну** группу-источник и при появлении **свежей** записи (не в lip, не старше порога) **репостить или копировать** на главные стены активных регионов.

### Решение
- Новый модуль `modules/copy_setka_network.py` + параметры только из **env** (`COPY_SETKA_*` в `/etc/setka/setka.env`), без обязательного `RegionConfig`.
- Ветка в `tasks/parsing_scheduler_tasks.py`: при `region_code=='copy'` и `theme=='setka'` выполняется этот модуль; дедуп по `WorkTable(copy,setka).lip`.
- `VKPublisher.publish_repost`: параметр VK API — **`object`**, не `repost`; для групп передаётся `group_id`.
- Пул asyncpg: по умолчанию **меньше** (`DB_POOL_SIZE=3`, `DB_MAX_OVERFLOW=5`, `pool_recycle`), чтобы реже упираться в `max_connections` на VPS.

### Почему «перестали идти дайджесты» (кратко)
- Подтверждено: конфликт event loop + исчерпание слотов PostgreSQL мешали Celery-задачам; исправлено `run_coro` и перезапуском воркеров.
- Если снова «тишина» — проверить **VK**: `scripts/check_vk_token.py`, лимиты API, и таблицу `parsing_stats` / логи воркера.

---

## 2026-04-16 — Прод: парсинг/постинг не шли (Celery + БД)

### Симптомы
- По расписанию шли задачи (beat в порядке), но пайплайн парсинг → фильтр → постинг фактически не выполнялся.
- В `celery-worker.log`: `asyncpg.exceptions.TooManyConnectionsError` и `Future ... attached to a different loop` в `run_all_regions_theme` / SQLAlchemy.

### Причины
1. **`run_all_regions_theme`** создавал **новый event loop** на каждый запуск, тогда как остальные Celery-задачи используют **`run_coro`** (один loop на процесс воркера). Глобальный async engine/asyncpg оказывался привязан к другому loop → ошибка цикла и некорректное закрытие соединений.
2. **`parse_and_publish_theme`** после прошлого рефакторинга гонял async в **отдельном потоке** с отдельным loop — тот же конфликт с общим пулом соединений.
3. В **`database/connection.py`** был продублирован блок создания engine (мертвый код, риск путаницы при правках).

### Решения
- Все async-вызовы в `tasks/parsing_scheduler_tasks.py` переведены на **`run_coro`** (как в `correct_workflow_tasks` и `celery_app`).
- Удалён дубликат конфигурации в **`database/connection.py`**.

### Прод-деплой
- `git pull` на VPS, `systemctl restart setka setka-celery-worker setka-celery-beat`.

---

## 2026-04-13 — Исправления дайджеста: форматирование, токены, mourning

### Проблемы
1. Посты обрезались на полуслове в дайджесте
2. Публикация шла не тем токеном (Vita вместо Valstan)
3. Дайджест был "сплошным мясом" без разделения новостей
4. Траурные новости (СВО, смерть) перемешивались с позитивными

### Решения
- **No truncation**: посты, не влезающие целиком, пропускаются (на следующую итерацию)
- **Формат old_postopus**: `✍ текст` → ссылка-источник `[url|название]` → пустая строка (раньше `@url (source)`)
- **Token roles**: `VK_PUBLISH_TOKEN_NAME=VALSTAN` — только VALSTAN может публиковать
- **SentimentAnalyzer**: mourning detection (погиб, умер, СВО, прощание...)
- **DigestSplitter**: разделяет post-ы на mourning/regular перед билдингом
- **Mourning digest**: отдельный пост без заголовка (см. актуальное поведение выше)

### Файлы изменены
- `config/runtime.py` — VK_PUBLISH_TOKEN_NAME, get_publish_token(), validate_publish_token()
- `modules/publisher/digest_builder.py` — ✍ маркеры, no truncation, _format_post_entry()
- `modules/ai_analyzer/sentiment_analyzer.py` — MOURNING_MARKERS, label='mourning'
- `modules/publisher/digest_splitter.py` — НОВЫЙ: разделение по тональности
- `tasks/parsing_scheduler_tasks.py` — интеграция DigestSplitter в production pipeline
- `scripts/test_parse_run.py` — тест с разделением и двумя публикациями

### Результат теста
- 36 постов: 3 mourning → пост #580, 7 regular → пост #579
- Оба опубликованы через VALSTAN токен
- https://vk.com/wall-137760500_579 (обычный)
- https://vk.com/wall-137760500_580 (mourning)
