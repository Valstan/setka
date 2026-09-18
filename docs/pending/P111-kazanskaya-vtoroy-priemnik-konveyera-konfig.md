# 🎪 Казанская — второй приёмник конвейера: конфиг готов, ждёт grant'а; шаг accept стоит (mandate brain 2026-09-02/03, D-015 + D-061)

> Запись `P111` реестра [PENDING_FOLLOWUPS](../PENDING_FOLLOWUPS.md). Индекс грепается, запись читается целиком, реестр целиком не читается никогда ([D-097](../../AGENTS.md)).

`⏱ 2026-09-04 · snooze 0 · parked · условие расконсервации: письмо мозга «grant выдан» / первый pending в комнате setka`

**Сделано 04.09 (PR этой сессии):** сайт `kazanskaya` в `config/content_conveyor.py`
(endpoint `казанская.вмалмыже.рф/api/ingest/posts`, ключ `KAZANSKAYA_INGEST_KEY`, рубрики
`festival·prep·crafts·culture·history·other`), правила `rules/kazanskaya.md`, новое поле
`source_owner_ids` — из сводок `mi` берутся только посты РЦКД (`-217788511`, «ДК Малмыжа»).
Паблика «Сабантуй — Казанская» в пуле сбора нет — попросить id у Казанской через мозг.
Приём входящих выдач: `scripts/accept_secret_grants.py` + `modules/secrets_grants.py`
(allowlist `KAZANSKAYA_INGEST_KEY ← kazanskayamalmyzh`; поля `aliasKey`/`sourceSlug`/`state`
сверены с кодом КАРМАНа read-only; 12 тестов). Отчёт мозгу:
`mailbox/to-brain/2026-09-04-kazanskaya-config-ready-accept-step-built-six-esa-lines.md`.

**Что дальше, по письму мозга:**
1. Прогнать на боксе `accept_secret_grants.py --list` → `--dry-run` → без флагов
   (под `setka.env` + `secrets-token.env`, форма из handoff §Failed approaches).
2. Рестарт worker'а (bootstrap довезёт ключ), `CONVEYOR_SITES=vmalmyzhe,kazanskaya` в env.
3. Сначала dry-run раннера по `kazanskaya` — показать мозгу, что поедет первым.
4. Отчёт: «accept стоит, ключ принят, конфиг включён».

**Ловушки:** `KAZANSKAYA` в `gateway_keys` — ключ ПОТРЕБИТЕЛЯ (их сайт → наш шлюз), а
`KAZANSKAYA_INGEST_KEY` — ключ ПРИЁМНИКА (мы → их сайт); разные значения. У КАРМАНа
свой ключ комнаты с тем же именем заслоняет grant (`shadowed`) — если accept даст 409
«имя занято», смотреть `GET /api/secrets` на своё значение.
