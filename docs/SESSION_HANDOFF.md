# Session Handoff

> Sticky-note для непрерывности между сессиями разработки SETKA. Перезаписывается через [`/close_session`](../.claude/commands/close_session.md) — историю смотри через `git log --follow -- docs/SESSION_HANDOFF.md`.

**Status:** ACTIVE (обе крупные нитки задеплоены; HITL-рутина заведена и живёт — копится agree-rate; Радар-ID ждёт trener)
**Updated:** 2026-07-07
**Branch:** main
**Last release in prod:** код HEAD **`44adbc9`** (#308). Прод-состояние без изменений в эту сессию — работа была операционная (завёл рутину + засеял вердикты), кода/миграций не трогал.

---

## Текущая нитка

Обе крупные нитки построены и задеплоены ранее. За эту сессию **разблокирован HITL** — рутина классификации заведена и проверена end-to-end (раньше «ждала владельца»):

1. **HITL-классификатор** — рутина `setka-hitl-classifier` (локальный scheduled agent Claude Desktop,
   cron `0 */3 * * *`) сама ходит к прод-API `/api/classifier`, классифицирует посты района `mi`,
   шлёт вердикты в ленту `/classifier`. Живой прогон 2026-07-06 положил 8 вердиктов. Теперь **дело за
   оператором** — разбирать ленту (✅/✎), копится agree-rate по типам.
2. **Радар-ID (OIDC-SSO)** — Ф1 живёт на проде (`вход.вмалмыже.рф`, TLS). Без изменений: ждёт, что
   trener построит свою сторону → round-trip-smoke.

## Следующий шаг

Кода не требуют — за внешними участниками / оператором:

1. **HITL:** оператор разбирает ленту `/classifier` (меню «Система» → Классификатор). Рутина сама
   досыпает вердикты каждые 3 часа **пока открыто приложение Claude на этой машине**. Если захотим
   истинную облачную 24/7 — завести scheduled cloud agent на claude.ai/code по
   `docs/ops/hitl-classifier-routine.md` (промпт рутины уже отлажен — можно скопировать из локальной
   задачи `~/.claude/scheduled-tasks/setka-hitl-classifier/SKILL.md`).
2. **Радар-ID:** дождаться trener (redirect `/auth/vk/callback`), прогнать round-trip. Контракт у brain.
3. Если обе на паузе — из PENDING: Кругозор 2×-эксперимент, discovery error_code persist (#041),
   VK-шлюз v2-запись (ждёт scope-решений владельца).

## Контекст

- **План:** HITL — `docs/adr/0003-hitl-content-classifier.md` + `docs/ops/hitl-classifier-routine.md`;
  Радар-ID — `docs/adr/0002-radar-sso-oidc-provider.md`.
- **Связанные коммиты сессии:** только этот handoff-PR (doc-only). Прод-код не менялся.
- **Операционные факты сессии (не в git):**
  - Заведена локальная рутина `setka-hitl-classifier` (cron `0 */3 * * *`); ключ `CLASSIFIER_INGEST_KEY`
    зашит в её промпт. Управление — раздел «Scheduled» в сайдбаре Claude Desktop.
  - Засеяно 8 вердиктов вручную на проде (`content_classifications`, все shadow, ничего не
    удаляют/публикуют).
- **Прод:** все сервисы active, health 200. Таблицы классификатора: `content_classifications` (8 строк),
  `classification_corrections` (0). Реальные имена таблиц — эти (НЕ `classifier_verdicts`).
- **Открытых PR:** этот handoff-PR (doc-only, авто-merge).

## Failed approaches (этой нитки)

- **`source /etc/setka/setka.env` под обычным ssh-юзером** — env root-only, `source` молча читает
  пустоту → ложный вывод «ключ не задан / region пуст». **Читать env только через `sudo bash -c
  "source ...; echo \$VAR"`** (память `curate-prod-env-sourcing`).
- **`python3 ... << PYEOF > /tmp/f.json` с `json.dump` в тот же файл** — shell-redirect и `open(...,"w")`
  дерутся, файл бьётся (в него попадает и `print`, и частичный json). **Разделять:** питон пишет только
  через `json.dump`, диагностику — отдельной строкой без redirect в тот же путь.

## Открытые вопросы для пользователя

- **HITL Этап 2** (позже): какие VK-сообщества-источники наполняют вмалмыже.рф — нужно при переходе к
  вёрстке сайтов. На обкатку не влияет.

## Не забыть (low-priority)

- 🟢 **HITL истинно-облачная рутина:** локальная работает только при открытом приложении. Когда нужно
  24/7 — перенести промпт из `~/.claude/scheduled-tasks/setka-hitl-classifier/SKILL.md` в scheduled
  cloud agent на claude.ai/code.
- 🟢 **HITL enforce:** agree-rate по типу ≥90%/≥2нед → авто-действие; при `ANTHROPIC_API_KEY` рутину → Celery-таск.
- 🟢 Радар-ID: RS256-ключ — кандидат №1 на зеркало в Карман (ADR-0006), когда KARMAN даст mirror-API.
- 🟢 Следующий dead-code прогон ~2026-07-14.
- 🛠 git push на этой машине: HTTP/2+schannel флапает — обход `git -c http.version=HTTP/1.1 push`.

---

> Если читаешь это в начале новой сессии — обнови через `/close_session` в конце. **Не аккумулируй history тут** — она в `git log --follow -- docs/SESSION_HANDOFF.md`.
