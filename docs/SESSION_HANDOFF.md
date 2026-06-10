# Session Handoff

> Sticky-note для непрерывности между сессиями разработки SETKA. Перезаписывается через [`/close_session`](../.claude/commands/close_session.md) — историю смотри через `git log --follow -- docs/SESSION_HANDOFF.md`.

**Status:** ACTIVE
**Updated:** 2026-06-10
**Branch:** main
**Last release in prod:** прод на `69c2360` — tiered-поиск #035 задеплоен (миграция 036 `pg_trgm`, restart web); worker/beat не трогались с `adf563a`.

---

## Текущая нитка

Кодовых ниток в полёте нет — всё построенное смержено и задеплоено. Три нитки в **фазе наблюдения/ожидания**:

1. **Tiered-поиск #035** — построен и задеплоен целиком за сессию 2026-06-10 ([PR #191](https://github.com/Valstan/setka/pull/191)): shared `web/static/js/search_match.js` + серверный tiered (`/ad-crm`, `/posts`) + поле поиска на `/communities` + миграция 036 (`pg_trgm`). Остаток — браузер-верификация владельцем. Brain получил report (`mailbox/to-brain/2026-06-10-tiered-search-shipped.md`).
2. **PoC LLM-курации (shadow, регион `mi`)** — ждёт накопления вердиктов до ~2026-06-14, затем `--stats` → ack brain с цифрами.
3. **near-dup Jaccard дедуп** — наблюдение логов (`ssh setka "grep 'near-dup (jaccard) drop' /home/valstan/SETKA/logs/celery-worker.log | tail"`).

Также в сессии 2026-06-10 обработаны 3 recommend-директивы brain ([PR #190](https://github.com/Valstan/setka/pull/190)): `/start` пересобран (sync ДО handoff, pool #032), PENDING получил метки старения (#033), consolidation-probe метрики ушли brain'у.

## Следующий шаг

Приоритет за владельцем:

1. **~2026-06-14: цифры PoC курации** — `ssh setka "cd /home/valstan/SETKA && ./venv/bin/python scripts/curate_pending.py --stats"` → flag-rate/precision/токены → письмо-ack brain'у (он ждёт).
2. **Браузер-проход по чек-листу** «Пакет браузер-верификаций владельцем» (`PENDING_FOLLOWUPS.md`, 🟢 Идеи) — теперь включает tiered-поиск (середина слова / опечатка / EN-раскладка / номер с дефисами).
3. **Освежить `nolinsk`** — верх 🔴 очереди в [`REGION_REFRESH_LOG.md`](REGION_REFRESH_LOG.md) (далее vp/arbazh/bal/nema/klz/kukmor).

## Контекст

- **План:** нет активного файла-плана; roadmap — `PENDING_FOLLOWUPS.md`, очередь регионов — `REGION_REFRESH_LOG.md`.
- **Связанные коммиты сессии:** `671e1bc` #190 (3 директивы: sync-order, aging, план #035, probe), `69c2360` #191 (tiered-поиск Ф0–Ф3).
- **Прод:** HEAD `69c2360`, 3/3 active, health 200. Миграция 036 применена (`pg_trgm` стоит); migrate.py заодно дозаписал в журнал 034/035 (применялись вручную, идемпотентные). Smoke: `search_match.js` 200, `/api/ad-crm/clients?q=` 200, `/api/posts/?q=` 200.
- **Открытых PR:** нет (handoff-PR этой сессии — doc-only, авто-merge).

## Failed approaches (этой нитки)

- **Подсветка совпадения в имени на `/communities`** — невозможна: ячейка имени — редактируемый `<input>` (inline-rename), внутрь input HTML не вставить. Поиск работает, подсветка пропущена осознанно (решение «везде, где дёшево» это допускает).
- Прочих отвергнутых подходов в сессии не было; уроки наблюдения (мёртвые фильтры `/posts`, клиентский поиск поверх серверной пагинации) — зафиксированы в письме brain и PR #191.

## Открытые вопросы для пользователя

_Нет._

## Не забыть (low-priority)

- 🟢 «Пакет браузер-верификаций владельцем» — консолидированный чек-лист в PENDING (🟢 Идеи): один ~20-минутный проход или вычеркнуть то, чем уже пользуешься.
- 🟢 `/posts`: серверный `q` ищет по всей базе, но кандидат на доработку — серверная подсветка за пределами первых 100 символов превью (сейчас подсвечивается только видимая часть).
- ⏸ AI-дедуп тяжёлого перефраза (embeddings) — `parked` до апгрейда VPS ≥4 ГБ (метка в PENDING).

---

> Если читаешь это в начале новой сессии — обнови через `/close_session` в конце. **Не аккумулируй history тут** — она в `git log --follow -- docs/SESSION_HANDOFF.md`.
