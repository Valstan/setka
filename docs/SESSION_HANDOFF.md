# Session Handoff

> Sticky-note для непрерывности между сессиями разработки SETKA. Перезаписывается через [`/close_session`](../.claude/commands/close_session.md) — историю смотри через `git log --follow -- docs/SESSION_HANDOFF.md`.

**Status:** IDLE
**Updated:** 2026-06-05
**Branch:** main
**Last release in prod:** прод на `4a49bbe` (#148). Heartbeat #018 воскрешён и **верифицирован вживую** (ключи `setka:digest_last_published:{addons,novost,union}` пишутся, watchdog novost → `fresh` после волны 11:40). 3/3 сервиса active.

---

## Текущая нитка

_Нет активной нитки — все три потока сессии 2026-06-05 закрыты, задеплоены и верифицированы:_
1. **`/obriv`** (кросс-проектный мандат brain #021) — команда восстановления после обрыва.
2. **Веб-дашборд** (idea brain #1) — расширение `/monitoring` (heartbeat #018 в UI, liveness Celery, ручное управление).
3. **Фикс heartbeat #018** — watchdog был молча мёртв с 2026-06-03; найден и устранён реальный корень.

Открытая стартовая позиция.

## Следующий шаг

Кандидатные стартовые точки (приоритет — за владельцем):

1. **Браузер-верификация владельцем** новых блоков `/monitoring` (агент в UI не ходит): 💓 heartbeat-таблица (должна показывать addons/novost/union/… свежими), 🫀 liveness воркеров (ping), 🎛️ ручное управление (скан региона / стоп workflow под `confirm()`).
2. **AI-дедуп новостей** — отложен владельцем; путь без бюджета: локальные embedding-модели (`multilingual-e5`/`LaBSE`, CPU, без geo-блока). Когда возьмёшься — отписать brain выбранный эмбеддер (tech-radar).
3. **Точечный добор источников** регионов через `/discover_communities` (длинный хвост СДК/библиотек).

## Контекст

- **План:** отдельного файла-плана нет.
- **Связанные коммиты/PR сессии (все на проде):**
  - `4a49bbe`/[#148](https://github.com/Valstan/setka/pull/148) — **реальный корень** heartbeat-бага: `publish_digest()` возвращает dict, а 3 call-site звали `.success` атрибутом → `AttributeError` до трекинга. Хелпер `monitoring.metrics.publish_result_label()`. +5 тестов.
  - `3878de9`/[#147](https://github.com/Valstan/setka/pull/147) — fork-safe Redis (PID-guard) + **немые `debug`→`warning`** (это вскрыло traceback). +1 тест.
  - `31ff22d`/[#146](https://github.com/Valstan/setka/pull/146) — heartbeat перед Prometheus, независимо (попутный hardening). +1 тест.
  - `02cdb36`/[#145](https://github.com/Valstan/setka/pull/145) — `/obriv` + расширение `/monitoring` (heartbeat/liveness/контроль). +10 тестов.
- **Прод:** HEAD `4a49bbe`, 3/3 active, health 200. Без невыполненных миграций (вся сессия — без миграций). 939 тестов зелёные на main.
- **Открытых PR:** doc-only handoff-PR этого `/close_session` (авто-merge). Кодовых открытых PR нет.

## Failed approaches (этой нитки)

- **Heartbeat-баг ≠ порядок Prometheus/heartbeat** (#146) и **≠ fork-safety Redis** (#147) — обе гипотезы проверены и отвергнуты (fork-probe писал/читал нормально; изолированный `track_digest_published` работал). Реальный корень — `AttributeError` на `dict.success` (#148). **Урок:** не чинить вслепую по гипотезе — сначала сделать сбой видимым (это и сделал #147, переведя немые `debug` на `warning`), затем читать реальный traceback.

## Открытые вопросы для пользователя

- Следующая нитка: AI-дедуп (локальные embeddings), добор источников, или что-то иное?

## Не забыть (low-priority)

- 🟢 **Браузер-верификация владельцем** новых виджетов `/monitoring` + (ранее) `/ad-crm`, планировщик кабинета 2.0 — логика под кнопками доказана тестами + прод round-trip'ом.
- 🟢 **Доклад brain отправлен** (`mailbox/to-brain/2026-06-05-dashboard-caught-dead-watchdog.md`) — два урока (liveness-дашборд валидирует watchdog; `best-effort + debug`-глушилка прячет сломанную фичу). Кандидаты brain в pool/GOTCHAS — на его усмотрение.
- 🟢 **AI-дедуп** остаётся отложенным (нулевой бюджет, локальные embeddings).

---

> Если читаешь это в начале новой сессии — обнови через `/close_session` в конце. **Не аккумулируй history тут** — она в `git log --follow -- docs/SESSION_HANDOFF.md`.
