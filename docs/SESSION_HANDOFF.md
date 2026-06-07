# Session Handoff

> Sticky-note для непрерывности между сессиями разработки SETKA. Перезаписывается через [`/close_session`](../.claude/commands/close_session.md) — историю смотри через `git log --follow -- docs/SESSION_HANDOFF.md`.

**Status:** ACTIVE
**Updated:** 2026-06-07
**Branch:** main
**Last release in prod:** прод на `adf563a` — near-dup Jaccard дедуп + env-тюнинг задеплоены (restart worker/beat); ранее в сессии — shadow LLM-курация (`fe27360`, миграция 035 применена, env-флаги внесены, регион `mi`).

---

## Текущая нитка

Две задеплоенные фичи в **фазе наблюдения** (код шиплён, мид-имплементации нет):

1. **PoC LLM-курации дайджестов (shadow, регион `mi`)** — [#186](https://github.com/Valstan/setka/pull/186). Ответ на `suggest` brain (письмо 2026-06-07). Дизайн скорректирован: enforcing→**shadow** (публикуем как сейчас, мерим, сколько LLM бы отсеяла; fail-open). На проде: миграция 035 `digest_curation_runs` применена, env `DIGEST_CURATION_SHADOW_ENABLED=1` + `DIGEST_CURATION_REGION_CODES=mi`, рутина `/loop 30m /curate` на Haiku (локальный дефолт машины в `.claude/settings.local.json`, не коммитится).
2. **near-dup Jaccard дедуп** — [#187](https://github.com/Valstan/setka/pull/187), задеплоен на `adf563a`. SimHash near-dup уже работал (в `advanced_parser`, не `detector.py`); добавлен intra-batch Jaccard (ловит переставленные/переписанные дубли) + env-тюнинг + счётчики. ON by default, консервативно.

## Следующий шаг

Приоритет за владельцем. Конкретные follow-up'ы:

1. **Наблюдать Jaccard на проде** пару дней: `ssh setka "grep 'near-dup (jaccard) drop' /home/valstan/SETKA/logs/celery-worker.log | tail"`. Режет лишнее → поднять `DIGEST_JACCARD_THRESHOLD=0.92` или выключить `DIGEST_JACCARD_DEDUP_ENABLED=0` в `/etc/setka/setka.env` + restart worker (без передеплоя).
2. **PoC курации:** первая shadow-строка ляжет с ближайшей публикацией `mi`; проверить рутину `/curate` (и что Haiku подхватился на первом автозапуске). Через ~неделю — `scripts/curate_pending.py --stats` → flag-rate/precision/токены → **ack brain'у с цифрами** (письмо-feedback уже ушло, ждёт данных).
3. **Освежить `nolinsk`** — верх 🔴 очереди в [`REGION_REFRESH_LOG.md`](REGION_REFRESH_LOG.md) (далее vp/arbazh/bal/nema/klz/kukmor). Процесс: localities из ru.wikipedia → 2 прохода `discover_scan` → классификация по постам → seed в пробелы.

## Контекст

- **План:** нет активного файла-плана; roadmap'ы — в `PENDING_FOLLOWUPS.md`, очередь регионов — в `REGION_REFRESH_LOG.md`.
- **Связанные коммиты сессии:** `fe27360` #186 (shadow LLM-curation PoC), `adf563a` #187 (near-dup Jaccard + env-tuning).
- **Прод:** HEAD `adf563a`, 3/3 active, health 200. Миграция 035 применена. Env-добавления: `DIGEST_CURATION_*` (бэкап `/etc/setka/setka.env.bak.20260607`). near-dup Jaccard ON по дефолту.
- **Открытых PR:** нет (оба смержены; handoff-PR этой сессии — отдельно, doc-only).

## Failed approaches (этой нитки)

- **Enforcing-схема курации (публиковать только approved)** — отвергнута на этапе оценки: сцепляет публикацию с доступностью desktop'а (вечерние волны 21/23 MSK рискуют протухнуть, [G26](../../brain_matrica/cross-project-ideas/GOTCHAS.md)). Заменена shadow-режимом (measure-before-enforce). Не возвращаться к enforcing без fail-open.
- **Слепое ужесточение SimHash-порога** для ловли перефраза — отвергнуто: риск срезать живые разные новости. Вместо этого — env-тюнинг (калибровка по данным) + intra-batch Jaccard (другой класс) + наблюдаемость.

## Открытые вопросы для пользователя

- Триаж «Автор: Валентин Савиных» в админ-подсказке VK на постах главных групп — разобрано (это владелец user-авторизованного community-токена, публичный автор = группа, не баг). Опционально: переезд на групповые API-ключи (косметика). Решения не требует, если не мешает.

## Не забыть (low-priority)

- 🟢 Браузер-верификации владельцем накопились: ad-cabinet серии (#152–165), планировщик отложки (B1/B2), CRM, subscriber-growth, тёмная тема, `/publications`, `/monitoring`.
- 🟢 subscriber-growth R3 (авто-анализатор динамики) — готов будет ~через неделю накопления снимков.
- ⏸ AI-дедуп тяжёлого перефраза (embeddings) — до апгрейда VPS ≥4ГБ.

---

> Если читаешь это в начале новой сессии — обнови через `/close_session` в конце. **Не аккумулируй history тут** — она в `git log --follow -- docs/SESSION_HANDOFF.md`.
