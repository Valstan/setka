# Session Handoff

> Sticky-note для непрерывности между сессиями разработки SETKA. Перезаписывается через [`/close_session`](../.claude/commands/close_session.md) — историю смотри через `git log --follow -- docs/SESSION_HANDOFF.md`.

**Status:** ACTIVE (HITL shadow копится; облачная рутина чинится от egress-403 — ждём проверки прогона)
**Updated:** 2026-07-07
**Branch:** main
**Last release in prod:** код HEAD **`bc0b8b2`** (#314). Кода эта сессия не меняла — только доки (#317, #318) + настройка рутины (вне репо).

---

## Текущая нитка

HITL-классификатор задеплоен, крутится в shadow. Эта сессия — **починка облачной рутины после egress-403**: облачное окружение рутины по умолчанию `Network access: Trusted` (пускает только пакеты, не произвольные хосты) → CONNECT к нашему API возвращал 403. Прод жив (health 200, оба хоста отдают 401 без ключа = API дошёл). Промпт рутины переведён на ASCII-хост `3931b3fe50ab.vps.myjino.ru` (punycode-IDN спотыкается об egress-прокси и вдобавок отвергается валидатором поля allowed-domains). Доки обновлены с точным UI-путём фикса.

Прод-факт этой сессии: `content_classifications` = 136 (было 96), все `source=routine`, последний вердикт 2026-07-07 18:35 MSK — до egress-блокировки рутина исправно писала.

## Следующий шаг

За владельцем (UI облачной рутины на claude.ai/code → страница рутины → ✏️):

1. **Network access:** `Trusted` → **`Custom`** → в allowed domains добавить `3931b3fe50ab.vps.myjino.ru` (или wildcard `*.vps.myjino.ru`, если один хост не примет). **Save changes.** NB: применяется к *новым* сессиям рутины, не задним числом. Punycode-хост в поле **не принимается** валидатором — и не нужен (промпт на ASCII).
2. **Вставить обновлённый промпт** рутины (ASCII-хост) — заготовка в [`docs/ops/hitl-classifier-routine.md`](ops/hitl-classifier-routine.md) §«Промпт рутины».
3. **Проверить прогон в :22** — в Runs 403 должен уйти; в ленте `/classifier` появятся новые вердикты `source=routine`. Эмпирика: `ssh setka "sudo -u postgres psql -d setka -tA -c \"SELECT source,MAX(created_at) FROM content_classifications GROUP BY source;\""` → время должно обновиться.
4. **Оператор** — разбирать ленту `/classifier` (копит agree-rate по типам).

## Контекст

- **План:** ADR-0003 (HITL), ADR-0004 (аудит обеих сторон), [`docs/ops/hitl-classifier-routine.md`](ops/hitl-classifier-routine.md) (§Troubleshooting — egress-403 → Network access Custom).
- **Связанные коммиты сессии:** `0d773df` (#317 — рутина на ASCII-хост + troubleshooting), `7629253` (#318 — точный UI-путь Network access → Custom).
- **Прод:** все сервисы active, health 200, HEAD `bc0b8b2`. `content_classifications` = 136.
- **Ключ рутины:** `CLASSIFIER_INGEST_KEY` в `/etc/setka/setka.env`; вшит в промпт рутины; **в git не кладём**.
- **Открытых PR:** этот handoff-PR (doc-only, авто-merge).

## Failed approaches (этой нитки)

- **Совет «добавь домены в allowlist на странице рутины»** (первая формулировка в #317) — неточен: реальный UI — диалог **Update cloud environment → Network access** (None/Trusted/Full/Custom), а не абстрактный «trusted domains». Исправлено в #318.
- **Punycode-хост `xn--b1ae3a1a.xn--80adkdyec4j.xn--p1ai` в allowed-domains** — валидатор поля отвергает («not a valid domain»). И на CONNECT egress-прокси режет его охотнее ASCII. Использовать только ASCII-хост.

## Открытые вопросы для пользователя

- **is_advertisement глобальна (#314):** отсев «продам/куплю» затрагивает ВСЕ районы. Если сельский район захочет оставлять «продам корову» — откат к per-region через `RegionConfig.delete_msg_blacklist`. Аудит есть только для `mi` → over-drop в других районах не виден.

## Не забыть (low-priority)

- 🟡 **Smoke-тест `/reliz` шаг 8.5 падает на 401** — `smoke_test.py` не умеет auth к session-защищённому diagnostics-эндпоинту. Гейт деплоя сломан. Фоновая задача заведена.
- 🟢 **Аудит обеих сторон** — расширить на другие районы (сейчас только `mi`).
- 🟢 **Enforce / Claude API** — при появлении `ANTHROPIC_API_KEY` рутину меняем на Celery-таск.
- 🟢 Радар-ID: ждёт trener (round-trip). Следующий dead-code прогон ~2026-07-14.
- 🛠 git push на этой машине: HTTP/2+schannel флапает — обход `git -c http.version=HTTP/1.1 push`.

---

> Если читаешь это в начале новой сессии — обнови через `/close_session` в конце. **Не аккумулируй history тут** — она в `git log --follow -- docs/SESSION_HANDOFF.md`.
