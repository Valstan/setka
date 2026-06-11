# /deadcode — ежемесячный гигиенический прогон dead-code (#036)

Гибрид «бесплатный статанализатор + LLM-триаж его находок» ([pool #036](../../../brain_matrica/cross-project-ideas/ideas/036-static-deadcode-gate-llm-triage.md)). **Report-only: никогда не авто-удалять.** Удаление мёртвого — обычным PR после триажа и решения владельца.

## Шаг 1. Прогон сканера

```bash
./venv/Scripts/python.exe -m pip show vulture >/dev/null 2>&1 || ./venv/Scripts/python.exe -m pip install vulture
./venv/Scripts/python.exe scripts/deadcode_scan.py
```

Сканер ([scripts/deadcode_scan.py](../../scripts/deadcode_scan.py)) сам собирает allowlist Celery-тасок (декораторы + строки `beat_schedule`) и framework-полей (pydantic/SQLAlchemy), гасит декораторы FastAPI/signals. Уже триаженные кандидаты подавлены через [scripts/deadcode_known.txt](../../scripts/deadcode_known.txt) — отчёт показывает **только новую дельту** с прошлого прогона.

`Итого: 0 кандидатов` → доложить «дельты нет», обновить дату прогона в PENDING, конец.

## Шаг 2. Триаж новых кандидатов (методика #028)

Для каждого нового кандидата — расследование, не слепой вердикт:

1. **grep по всему репо** (py + html + js): динамические употребления — строки, `getattr`, Jinja-шаблоны, JS-вызовы API, `response_model=`, строковые имена Celery. tests/ сканером не покрыты — употребление только в тестах = `test-only`.
2. **git-история символа** (`git log --oneline -3 -S "<name>" -- <file>`): потребитель удалён рефактором (`dead` — хвост) или никогда не был подключён (`sleeping` — спящая фича)?
3. Вердикт: `dead` / `test-only` / `alive` (false positive) / `sleeping` / `uncertain`.

Много кандидатов (>15) — разбить на пакеты и пустить параллельных read-only агентов (как в первом триаже 2026-06-10).

## Шаг 3. Зафиксировать вердикты

- Каждый вердикт → строка в `scripts/deadcode_known.txt`: `file::symbol  # вердикт — заметка`.
- `sleeping` (живое, но не подключённое) → **не удалять молча**: запись в `PENDING_FOLLOWUPS.md` на re-триаж по #033 (возобновить / переформулировать / выкинуть — решение владельца).
- `dead` → предложить владельцу пакет на удаление (отдельный PR; при удалении идти по транзитивной цепочке orphan'ов, не только по флагам — см. #028).

## Шаг 4. Отчёт и хвосты

- Доложить: сколько новых, разбивка по вердиктам, заметные находки.
- Коммит `chore(deadcode): monthly scan YYYY-MM — N new candidates triaged` через PR под обычными гейтами.
- Обновить дату прогона в `PENDING_FOLLOWUPS.md` (метка старения пункта «ежемесячный deadcode-прогон»).

## Что НЕЛЬЗЯ

- ❌ Авто-удалять код по выхлопу сканера (false positives у фреймворков неизбежны).
- ❌ Делать сканер блокирующим гейтом CI (#036: report-only, иначе источник боли).
- ❌ Удалять `sleeping`-фичи без решения владельца.
