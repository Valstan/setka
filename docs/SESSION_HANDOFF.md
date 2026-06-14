# Session Handoff

> Sticky-note для непрерывности между сессиями разработки SETKA. Перезаписывается через [`/close_session`](../.claude/commands/close_session.md) — историю смотри через `git log --follow -- docs/SESSION_HANDOFF.md`.

**Status:** ACTIVE (нет mid-flight стройки — всё задеплоено; активны standing-нитки, ждущие ввода владельца / brain)
**Updated:** 2026-06-14
**Branch:** main
**Last release in prod:** прод на `451b2e0` (= main); web+worker+beat перезапущены, миграция 044 применена, health 200. Все 7 PR сессии задеплоены инкрементально.

---

## Текущая нитка

Mid-flight стройки нет. Сессия 2026-06-14 (вторая за день): **7 PR** ([#241](https://github.com/Valstan/setka/pull/241)–[#247](https://github.com/Valstan/setka/pull/247)) — закрыты обе brain-директивы рассылки/обложек (одна построена, вторая на probe-гейте у brain), гигиена и QA. Активные нитки ждут **ввода владельца / brain**, кода mid-flight нет.

**Что сделано и живёт в проде:**
- **Авто-приветствие рекламодателю — ВКЛючено на все группы** ([#241](https://github.com/Valstan/setka/pull/241)): wildcard `AD_AUTO_GREETING_COMMUNITIES=*` + текст (Вариант А). env на проде, beat `10,40 8-22` живёт.
- **Сетевая рассылка — построена целиком** (probe [#242](https://github.com/Valstan/setka/pull/242) → MVP [#243](https://github.com/Valstan/setka/pull/243) → QA [#246](https://github.com/Valstan/setka/pull/246)): `modules/broadcast/` + миграция 044 + beat `broadcast-dispatch`/`broadcast-watchdog` + `/broadcast`. Свой беат `wall.post` немедленно (НЕ VK-отложка), idempotency per-(цель,прогон) ON CONFLICT + reclaim stale-pending, throttle ≥5с, повтор, watchdog #018.
- **Probe cover-API** ([#244](https://github.com/Valstan/setka/pull/244), `scripts/probe_cover_api.py`): 16/16 пабликов `can_set` через user-токен владельца, 5 без обложки (вкл. Верхошижемье). Ответ brain отправлен.
- **Dead-code прогон 2026-06** ([#245](https://github.com/Valstan/setka/pull/245)): удалены 2 хвоста (`format_number`, модуль `image_utils`), 9 fp/sleeping подавлены.
- **QA-фиксы прод-постинга** ([#246](https://github.com/Valstan/setka/pull/246) рассылка, [#247](https://github.com/Valstan/setka/pull/247) ad-публикация): закрыты дубль-на-двойной-клик и зависание кампании из adversarial-ревью.

## Следующий шаг

Mid-flight задачи нет. Кандидаты (по приоритету):
1. **Обложки сообществ — ждём brain↔владельца.** Probe готов (16/16 can_set, референсы в `mailbox/to-brain/2026-06-14-community-cover-api-probe.md`). Следующий ход НЕ мой: brain собирает промт фона по 11 референс-обложкам → владелец генерит фон Верхошижемье. **Когда придёт фон** — строить сборщик: `modules/...` Pillow-композит (фон 1920×768 + название района + брендинг) → `photos.getOwnerCoverPhotoUploadServer`+`saveOwnerCoverPhoto`, пилот Верхошижемье (`club221515888`, обложки нет).
2. **Браузер-проверки владельцем** (наружу-действия, за владельцем): `/broadcast` (собрать тест-кампанию на 1-2 паблика → запланировать на ближайшую минуту → пост вышел, статус «опубликован»); кнопка «Опубликовать»/«Оформить» ad-CRM.
3. **LLM-курация Фаза 2** — ждёт спот-чек владельца на 19 флагах (precision) + решение enforcing vs Haiku-API.
4. **Сетевая рассылка — вариация per-target** (`vary_per_target` — forward-compat поле, off): дизайн «лёгкой вариации» за brain/владельцем (анти-dup-detection), строить только когда понадобится.

## Контекст

- **План:** активного плана-файла нет; нитки вели по brain-письмам + запросам владельца.
- **Связанные коммиты сессии:** `2253627` авто-приветствие wildcard · `b075922` probe-ответ рассылки · `f6a1fa2` рассылка MVP (миграция 044) · `af2ad75` probe cover-API · `9bfcf3e` dead-code · `af83a9c` QA диспетчера рассылки · `451b2e0` идемпотентность ad-публикации.
- **Прод-правки вне git (записаны — применить при чистой переустановке):** `/etc/setka/setka.env` — `AD_AUTO_GREETING_COMMUNITIES=*` + `AD_AUTO_GREETING_TEXT="…"` (в кавычках!); миграция 044 применена; бэкап env `setka.env.bak-20260614-cover`.
- **Прод:** все сервисы active, HEAD `451b2e0`, health 200. Рассылка-диспетчер тикает (no-op, кампаний нет).
- **Открытые brain-письма (входящие):** обе директивы 2026-06-14 (рассылка/обложки) обработаны — ответы в `mailbox/to-brain/`.
- **Отчёты brain'у этой сессии:** `mailbox/to-brain/`: `2026-06-14-network-broadcast-probe-and-plan.md`, `2026-06-14-community-cover-api-probe.md`, `2026-06-14-env-quoting-and-oneclick-idempotency.md` (находки #009).
- **Открытых PR:** этот handoff-PR (doc-only, авто-merge).

## Failed approaches (этой нитки)

- **Авто-репост стале-pending claim'ов в диспетчере рассылки** — отвергнуто: статус внешней публикации после краха неизвестен → переисполнение = риск дубля. Реклеймим в `error` (терминально, БЕЗ переписи), оператор дожимает через retry.
- **`getOwnerCoverPhotoUploadServer` под community-токеном как probe прав** — не понадобилось: user-токен владельца (админ везде) даёт `can_set` на всех 16, community-токен не нужен. Постим cover как владелец-админ, не «от группы».

## Открытые вопросы для пользователя

- Обложки: дождаться промта brain'а (по 11 референсам) + сгенерить фон Верхошижемье — тогда строю сборщик.
- LLM-курация Фаза 2: спот-чек на 19 флагах + enforcing vs Haiku-API.
- Безопасность (висит с прошлой сессии): токен AFONYA мелькал в транскрипте — владелец решил **не** перевыпускать.

## Не забыть (low-priority)

- 🟢 Браузер-проверка `/broadcast` владельцем перед первой реальной рассылкой.
- 🟢 5 Prometheus-метрик без продьюсера (`sleeping` в `deadcode_known.txt`) — чистка при желании владельца.
- 🟢 Следующий dead-code прогон ~2026-07-14.

---

> Если читаешь это в начале новой сессии — обнови через `/close_session` в конце. **Не аккумулируй history тут** — она в `git log --follow -- docs/SESSION_HANDOFF.md`.
