# HITL-классификатор: облачная рутина (этап B) — настройка

> **Что это.** Пока нет `ANTHROPIC_API_KEY` (Claude API из Celery — этап enforce), классификацию в
> shadow делает **облачная рутина** (scheduled cloud agent на claude.ai/code): по расписанию забирает
> батч постов из setka, классифицирует, возвращает вердикты. Крутится в облаке Anthropic → не зависит от
> включённого компа, ноль permission-промптов на боксе. Данные ходят через HTTP-интерфейс
> `/api/classifier` (защита — X-API-Key рутины). Дизайн — [ADR-0003](../adr/0003-hitl-content-classifier.md),
> вариант B.

## Что нужно на проде (один раз)

1. **Секрет рутины** в `/etc/setka/setka.env` (#008):
   ```
   CLASSIFIER_INGEST_KEY=<длинная-случайная-строка>
   CLASSIFIER_REGION_CODES=mi          # один район для обкатки (код региона)
   # CLASSIFIER_DISABLED=1             # аварийный kill-switch (по умолчанию выкл.)

   # Аудит сбора (ADR-0004, вариант B) — источник ОБЕИХ сторон сбора:
   COLLECTION_AUDIT_SHADOW_ENABLED=1   # включить запись собранных постов
   COLLECTION_AUDIT_REGION_CODES=mi    # тот же район, что у классификатора
   ```
   Сгенерировать ключ: `openssl rand -base64 32`. Аудит сбора пишется в Celery-воркере
   (там идёт сбор) → после включения нужен `systemctl restart setka-celery-worker` +
   `setka` (web читает аудит). Fail-safe: сбой аудита НЕ ломает сбор/публикацию.
2. **Миграция 053** (`053_hitl_classifier_shadow.sql`) применена + `restart web`.
3. Проверка снаружи (с ключом):
   ```
   curl -s -H "X-API-Key: $KEY" "https://вход.вмалмыже.рф/api/classifier/pending?limit=3"
   ```
   должен вернуть `{"count": N, "posts": [...]}`.

## HTTP-контракт (что зовёт рутина)

Базовый URL для **облачной рутины** — ASCII-хост `https://3931b3fe50ab.vps.myjino.ru`
(тот же VPS; punycode-IDN `xn--b1ae3a1a.xn--80adkdyec4j.xn--p1ai` = `вход.вмалмыже.рф` тоже
работает, но чаще спотыкается об egress-прокси облачного окружения — см. Troubleshooting ниже).
При переезде VPS myjino-хост меняется — сверяться с `docs/GATEWAY.md`. Все ingest-эндпоинты —
заголовок `X-API-Key: <CLASSIFIER_INGEST_KEY>`.

**Источник постов** — аудит сбора `collected_post_audit` (ADR-0004, вариант B): при сборе каждый
пост парков­ается с решением фильтра — `kept` (прошёл, кандидат в публикацию) или `dropped` +
причина (`advertisement`|`blacklist_text`|`no_region_words`|`no_attachments`). Так классификатор видит
**обе стороны** (пере-публикацию и пере-фильтрацию), а не только опубликованное. Ключ поста — **`lip`**
(`"<owner_abs>_<post_id>"`, напр. `156168183_14260`). Fallback на `bulletin_curation_runs.candidates`
(только опубликованное), пока аудит не наполнился. Ответ `/pending` несёт `decision` + `drop_reason`.

| Метод | Путь | Назначение |
|---|---|---|
| `GET` | `/api/classifier/pending?region=<код>&limit=<N>` | Батч постов без вердикта. `region` пуст → берётся `CLASSIFIER_REGION_CODES`. Ответ: `{count, posts: [{lip, region_code, text, has_media, url}]}` |
| `GET` | `/api/classifier/postulates` | Текст файла-корректировщика (вставить в промпт) |
| `POST` | `/api/classifier/verdicts` | Вернуть вердикты. Тело: `{"verdicts": [{lip, theme, action, merge_with, split, confidence, reasoning, model, text, url, region_code}]}`. Ответ: `{recorded, skipped_existing, skipped_missing}` |

Схема вердикта (ADR-0003 §B):
- `lip` — ключ поста из `/pending`;
- `theme` — тема поста (в shadow **свободной строкой**);
- `action` — `publish` | `delete` | `hold`;
- `merge_with` — список **`lip`** постов **из этого же батча**, которые надо склеить с текущим по смыслу;
- `split` — `true`, если текущий пост сам ошибочная склейка (разъединить);
- `confidence` — 0..100; `reasoning` — 1 строка;
- **`text`/`url`/`region_code`** — эхо из `/pending` (сохранить снапшот; если не вернёшь — сервер
  доберёт из свод­ки, но эхо надёжнее).

## Промпт рутины (заготовка)

Создать scheduled cloud agent (claude.ai/code, расписание напр. каждые 2–3 часа) с таким заданием.
`{{KEY}}` подставить или хранить в секретах рутины.

```
Ты — классификатор постов районного новостного паблика (проект SARAFAN, shadow-фаза).
Работаешь строго по HTTP, ничего в systemd/БД напрямую не трогаешь.

Шаги за один прогон:
1. GET https://3931b3fe50ab.vps.myjino.ru/api/classifier/postulates
   (заголовок X-API-Key: {{KEY}}) — это классификационные постулаты, следуй им.
2. GET https://3931b3fe50ab.vps.myjino.ru/api/classifier/pending?limit=40
   (тот же X-API-Key) — батч постов. Если count=0 — заверши прогон, ничего не делай.
3. Для КАЖДОГО поста в батче определи вердикт по схеме:
   {lip, theme (свободная строка), action: publish|delete|hold,
    merge_with: [lip ...из этого же батча], split: true|false,
    confidence: 0..100, reasoning: одна строка,
    text, url, region_code (скопируй из объекта поста в /pending — это снапшот)}.
   merge_with заполняй списком lip, только если посты — про ОДНО событие. split=true,
   если пост сам склеен из разных тем (напр. спорт+похороны). Низкая уверенность → confidence < 60.
4. POST https://3931b3fe50ab.vps.myjino.ru/api/classifier/verdicts (X-API-Key), тело:
   {"verdicts": [ ...по одному объекту на пост... ]}.
5. Кратко отчитайся: сколько классифицировал, сколько publish/delete/hold, сколько merge/split.

ВАЖНО: это shadow — твои вердикты никого не удаляют и не публикуют, их разбирает оператор
в ленте. Классифицируй честно; пустой/мусорный пост — action=delete с обоснованием.
```

## Что видит оператор

Лента вердиктов — `/classifier` (меню «Система» → «Классификатор»). Пост + вердикт нейронки + кнопки
✅ Согласен / Изменить тему / → публиковать|удалить|отложить / Разъединить. Несогласия копятся в лог,
наверху — **agree-rate по типам** (тема/действие/склейка). Когда agree-rate по типу устойчиво ≥90%
(≥2 недели) — тип созрел для enforce (ADR-0003 §F).

## Troubleshooting

**403 на CONNECT от прокси облачного окружения** («organization egress-policy denial», рутина
рапортует «0 постов, техническая блокировка»). Это НЕ прод и НЕ сеть — **Network access** облачного
окружения рутины по умолчанию `Trusted` (пускает только скачивание пакетов из проверенных источников,
но НЕ произвольные хосты). Лечение:
1. **Разрешить наш хост в окружении рутины.** claude.ai/code → страница рутины → ✏️ →
   **Update cloud environment** → блок **Network access**. По умолчанию стоит `Trusted` — сменить на
   **`Custom`** («Create a list of allowed domains») и добавить хосты:
   `3931b3fe50ab.vps.myjino.ru` (и при желании punycode `xn--b1ae3a1a.xn--80adkdyec4j.xn--p1ai`).
   `Save changes`. **NB:** диалог пишет *«Changes will apply to new sessions»* — подхватится со
   следующего прогона, не задним числом. Альтернатива — `Full` (безлимит), но `Custom` безопаснее:
   рутине нужен ровно один хост.
2. В промпте рутины использовать ASCII-хост (`3931b3fe50ab.vps.myjino.ru`) как основной —
   заготовка выше уже на нём (punycode-IDN спотыкается об egress-прокси чаще).
3. Проверка, что дело не в проде: `curl -s -o /dev/null -w '%{http_code}'
   https://3931b3fe50ab.vps.myjino.ru/api/classifier/pending?limit=1` → `401` = API жив
   (нет ключа), значит блокирует именно окружение рутины.

## Переход на Claude API (enforce)

Когда появится `ANTHROPIC_API_KEY`: ingest-рутину выключаем (`CLASSIFIER_DISABLED=1` или снимаем ключ
рутины), Celery-таск зовёт Claude API и пишет в те же таблицы через `modules.classifier.service`. Лента,
метрика и файл-корректировщик не меняются — меняется только «мотор» классификации.
