# /curate — shadow LLM-курация дайджестов (PoC, письмо brain 2026-06-07)

Прогнать pending-прогоны из `digest_curation_runs` через рубрику релевантности и
записать per-post вердикт keep/drop. **Фаза 1 — shadow:** посты уже опубликованы,
мы лишь МЕРИМ, сколько LLM бы отсеяла (дельта над алгоритмом) и с какой точностью.
Ничего не публикуется и не удаляется этой командой — только UPDATE `verdicts`
(черту #025/#027 не трогаем).

Рассчитано на `/loop 60m /curate` локально (desktop включён днём, у сессии есть
SSH к проду). Данные — в **прод-БД**, поэтому скрипт гоняем через `ssh setka`.

## Шаг 0. Проверить, есть ли что курировать

```bash
ssh setka "cd /home/valstan/SETKA && ./venv/bin/python scripts/curate_pending.py --list --limit 20"
```

Пусто (`[]`) → доложить «нет pending-прогонов» и завершить (в режиме /loop —
просто тихо ждать следующего тика). Это нормальное состояние.

## Шаг 1. Прочитать рубрику

Прочитать [`docs/curation/rubric.md`](../../docs/curation/rubric.md) — общие
классы `drop` + per-region заметки. **Default = keep**, `drop` только при
уверенности (мы мерим precision — ложные срабатывания дороги).

## Шаг 2. Вынести вердикт по каждому прогону

Для каждого прогона из Шага 0: по каждому посту в `candidates` (есть `lip`,
`text`, `has_media`, `url`) реши `keep`/`drop` + короткую `reason` по рубрике.
Для дублей сравнивай посты **внутри одного прогона** между собой.

## Шаг 3. Записать вердикты

Сформировать JSON и записать (по одному прогону за вызов). `tokens_estimate` —
грубая оценка токенов, потраченных на этот прогон (для token-economy ack):

```bash
ssh setka "cd /home/valstan/SETKA && ./venv/bin/python scripts/curate_pending.py --apply" <<'JSON'
{"id": 12, "tokens_estimate": 700,
 "verdicts": [
   {"lip": "168170001_3005", "verdict": "keep", "reason": ""},
   {"lip": "168170001_3006", "verdict": "drop", "reason": "реклама"},
   {"lip": "168170001_3007", "verdict": "drop", "reason": "перефраз-дубль #168170001_3005"}
 ]}
JSON
```

Идемпотентно: повторный `--apply` по тому же `id` перезапишет вердикты.

## Шаг 4. Heartbeat + измерение (#018, token-economy)

После прогона показать агрегат — это и есть датапоинт для ack brain'у:

```bash
ssh setka "cd /home/valstan/SETKA && ./venv/bin/python scripts/curate_pending.py --stats"
```

Доложить кратко: обработано прогонов N / отсеяно (drop) M из K кандидатов
(flag-rate) / токенов ~T / топ-причины. Если по ревью нашлась системная ошибка
фильтра — **дописать правило/пример** в `docs/curation/rubric.md` (per-region
секцию): это и есть «обучение» рубрики.

## Когда хватит данных — ack brain'у

Накопив прогоны (≈неделя на 1 регионе), отправить feedback в
`mailbox/to-brain/` с цифрами: flag-rate, оценка precision (доля верных drop по
ручному ревью), токены/прогон. Затем решить Фазу 2 (enforcing с fail-open или
перенос фильтра в код на Haiku-API). См. `docs/PENDING_FOLLOWUPS.md`.
