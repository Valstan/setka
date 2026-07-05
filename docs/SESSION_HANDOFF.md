# Session Handoff

> Sticky-note для непрерывности между сессиями разработки SETKA. Перезаписывается через [`/close_session`](../.claude/commands/close_session.md) — историю смотри через `git log --follow -- docs/SESSION_HANDOFF.md`.

**Status:** ACTIVE (две нитки построены и задеплоены, обе ждут внешнего действия; mid-flight кода нет)
**Updated:** 2026-07-05
**Branch:** main
**Last release in prod:** код HEAD **`44adbc9`** (#308, HITL источник-фикс). Задеплоено сегодня:
Радар-ID Ф1 (миграция 052 + RS256-ключ + ВК-приложение 54666252 + домен вход.вмалмыже.рф + TLS),
discovery dormant (миграция 051), HITL-классификатор этап B (миграции 053+054 + env
`CLASSIFIER_INGEST_KEY`/`CLASSIFIER_REGION_CODES=mi`). #309 — doc-only, НЕ деплоился.

---

## Текущая нитка

Две крупные нитки построены и **задеплоены** за эту сессию, обе теперь ждут **внешнего** шага (не кода):

1. **Радар-ID (OIDC-SSO)** — Ф1 целиком построена (схема/ядро/ВК-вход) и живёт на проде
   (`вход.вмалмыже.рф`, TLS, discovery/jwks/authorize/token/userinfo, ВК-вход R16). Ждёт: brain форварднёт
   trener сигнал «строй свою сторону» → round-trip-smoke (#011) → пинг brain для подключения GONBA/Sabantuy.
2. **HITL-классификатор контента** — основа + этап B (облачная рутина как мост) построены и задеплоены;
   `/pending` отдаёт реальные посты района `mi`. Ждёт: владелец заводит scheduled cloud agent на
   claude.ai/code.

## Следующий шаг

Оба следующих шага — **за внешними участниками**, кода не требуют:

1. **HITL:** владелец заводит облачную рутину по `docs/ops/hitl-classifier-routine.md`
   (ключ: `ssh setka "sudo cat /etc/setka/classifier-routine-key.txt"`), затем разбирает ленту
   `/classifier` (меню «Система») — копится agree-rate по типам.
2. **Радар-ID:** дождаться, что trener реализует свою сторону (redirect `/auth/vk/callback`), прогнать
   round-trip. Пинговать brain не нужно — контракт у него.
3. Если оба на паузе — брать из PENDING: 🧹 discovery dormant-политика уже в проде (первый месячный
   digest сам придёт), VK-шлюз v2-запись (ждёт scope-решений владельца), Кругозор 2×-эксперимент.

## Контекст

- **План:** Радар-ID — `docs/adr/0002-radar-sso-oidc-provider.md`; HITL — `docs/adr/0003-hitl-content-classifier.md`.
- **Связанные коммиты сессии (все merged):**
  - `#299` sibling-read ADR-0007 + issuer-нит; `#300` discovery dormant-политика (задеплоено, миграция 051).
  - `#301/#302/#304` Радар-ID Ф1 (схема / OIDC-ядро / ВК-вход); `#303/#305` доки Радар-ID.
  - `#306` HITL дизайн (ADR-0003 + план brain); `#307` HITL основа (вариант B); `#308` HITL источник-фикс
    (свод­ки/lip); `#309` HITL задеплоен-статусы (doc).
- **Прод:** все сервисы active, health 200. Код HEAD `44adbc9`. Миграции 050–054 применены.
  Радар-ID и HITL-ingest живут; классификатор в shadow ничего не удаляет/публикует.
- **Открытых PR:** нет (этот handoff-PR — doc-only, авто-merge).

## Failed approaches (этой нитки)

- **HITL: строить классификатор на таблице `posts`** — попробовал в #307, при деплое `/pending` вернул 0:
  таблица `posts` ПУСТА (`monitor.py` не в celery beat). Живой источник — свод­ки
  `bulletin_curation_runs.candidates`, ключ `lip`. Исправлено в #308. **Урок: проверять, что источник
  реально наполнен, ДО постройки на предполагаемой ORM-таблице.**
- **TLS на боксе через certbot для нового поддомена** — acme-challenge не доходит (edge-прокси myjino
  терминирует HTTPS). Рабочий путь — привязка+TLS через панель Джино (память `jino-subdomain-tls-via-panel`).
- **`pre-commit run --all-files` до `git add` новых файлов** — пропускает untracked, commit-hook ловит
  F401 уже при коммите. **Запускать pre-commit ПОСЛЕ staging новых файлов.**

## Открытые вопросы для пользователя

- **HITL Этап 2** (позже): какие VK-сообщества-источники наполняют вмалмыже.рф («Малмыж инфо») — нужно
  при переходе к вёрстке сайтов. На этап B не влияет.

## Не забыть (low-priority)

- 📬 Письма-находки сессии (уедут к brain его `git pull`): `2026-07-05-radar-id-f1-deployed.md`,
  `2026-07-05-hitl-classifier-shadow-plan.md`, `2026-07-05-hitl-swappable-engine-bridge.md`.
- 🛠 git push на этой машине: HTTP/2+schannel флапает — обход `git -c http.version=HTTP/1.1 push`; `gh` API
  TLS-таймауты — ретраить.
- 🟢 Радар-ID: ключ подписи RS256 — кандидат №1 на зеркало в Карман (ADR-0006), когда KARMAN даст mirror-API.
- 🟢 HITL enforce: agree-rate по типу ≥90%/≥2нед → авто-действие; при `ANTHROPIC_API_KEY` рутину → Celery-таск.
- 🟢 Следующий dead-code прогон ~2026-07-14.

---

> Если читаешь это в начале новой сессии — обнови через `/close_session` в конце. **Не аккумулируй history тут** — она в `git log --follow -- docs/SESSION_HANDOFF.md`.
