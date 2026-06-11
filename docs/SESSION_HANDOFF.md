# Session Handoff

> Sticky-note для непрерывности между сессиями разработки SETKA. Перезаписывается через [`/close_session`](../.claude/commands/close_session.md) — историю смотри через `git log --follow -- docs/SESSION_HANDOFF.md`.

**Status:** ACTIVE
**Updated:** 2026-06-10
**Branch:** main
**Last release in prod:** прод на `69c2360` (tiered-поиск #035). PR #193/#194 — dev-инструментарий и доки, прод-деплоя не требуют.

---

## Текущая нитка

Кодовых ниток в полёте нет. В этой сессии закрыт sync-разрыв ([PR #193](https://github.com/Valstan/setka/pull/193) — rubric-заметки `mi`) и обработана директива brain #036 ([PR #194](https://github.com/Valstan/setka/pull/194) — vulture-сканер `scripts/deadcode_scan.py` + suppression-файл `scripts/deadcode_known.txt` + скилл `/deadcode` + первый полный триаж 167 кандидатов; ack brain'у ушёл в том же PR). Сессия пережила обрыв — `/obriv` довёл мерж #194 без потерь.

Три нитки в **фазе наблюдения/ожидания** (без изменений):

1. **PoC LLM-курации (shadow, регион `mi`)** — ждёт накопления вердиктов до ~2026-06-14, затем `--stats` → ack brain с цифрами.
2. **Tiered-поиск #035** — задеплоен, остаток — браузер-верификация владельцем.
3. **near-dup Jaccard дедуп** — мониторинг логов (`ssh setka "grep 'near-dup (jaccard) drop' /home/valstan/SETKA/logs/celery-worker.log | tail"`).

## Следующий шаг

Приоритет за владельцем:

1. **~2026-06-14: цифры PoC курации** — `ssh setka "cd /home/valstan/SETKA && ./venv/bin/python scripts/curate_pending.py --stats"` → flag-rate/precision/токены → письмо-ack brain'у (он ждёт).
2. **Чистка мёртвого кода по итогам триажа #036** — `scripts/deadcode_known.txt` содержит ~120 подтверждённо-мёртвых символов (слой postopus `modules/core/`, `tasks/vk_carousel_tasks.py`, старые wordpress/telegram-паблишеры). Удаление — отдельной веткой с ревью, report-only сканер сам ничего не удаляет.
3. **Освежить `nolinsk`** — верх 🔴 очереди в [`REGION_REFRESH_LOG.md`](REGION_REFRESH_LOG.md) (далее vp/arbazh/bal/nema/klz/kukmor).

## Контекст

- **План:** нет активного файла-плана; roadmap — `PENDING_FOLLOWUPS.md`, очередь регионов — `REGION_REFRESH_LOG.md`.
- **Связанные коммиты сессии:** `fa85540` #193 (rubric-заметки `mi`), `a981fba` #194 (deadcode #036: сканер + триаж + `/deadcode`; NB: subject squash-коммита битый «@ (#194)», полное описание — в теле коммита и PR).
- **Прод:** HEAD `69c2360`, 3/3 active, health 200 (probe этой сессии). #193/#194 деплоя не требуют.
- **Открытых PR:** нет (handoff-PR этой сессии — doc-only, авто-merge).
- **Ежемесячная рутина:** `/deadcode` — следующий прогон ~2026-07-10 (дельта против `deadcode_known.txt`).

## Failed approaches (этой нитки)

- _Не было._ (Нюанс на будущее: commit-subject не должен начинаться со стрей-символа — squash-merge GitHub взял дефектный subject `@ ...` как заголовок коммита на main, не исправить без force-push.)

## Открытые вопросы для пользователя

_Нет._

## Не забыть (low-priority)

- 🟢 «Пакет браузер-верификаций владельцем» — чек-лист в PENDING (🟢 Идеи), включая tiered-поиск.
- 🟢 Квартальный стратегический самоосмотр (#036, триггер 2) — первый: Q3 2026 (авг-сен), отдельная сессия → письмо brain.
- ⏸ AI-дедуп тяжёлого перефраза (embeddings) — `parked` до апгрейда VPS ≥4 ГБ.

---

> Если читаешь это в начале новой сессии — обнови через `/close_session` в конце. **Не аккумулируй history тут** — она в `git log --follow -- docs/SESSION_HANDOFF.md`.
