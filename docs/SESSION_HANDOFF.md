# Session Handoff

> Sticky-note для непрерывности между сессиями разработки SETKA. Перезаписывается через [`/close_session`](../.claude/commands/close_session.md) — историю смотри через `git log --follow -- docs/SESSION_HANDOFF.md`.

**Status:** IDLE
**Updated:** 2026-06-06
**Branch:** main
**Last release in prod:** прод на `33bdf6d` — всё этой сессии задеплоено и верифицировано: clipboard-fallback AI-черновика (#160, restart web), инфра снимков подписчиков (#161, миграция 031 + restart worker/beat) и фикс async-резолва токена (#162, restart worker). 3/3 сервиса active, health 200, первый снимок подписчиков засеян вживую (841/896).

---

## Текущая нитка

_Нет — приборка хвостов + инфра графика подписчиков закрыты, задеплоены и проверены round-trip'ом._ Открытая стартовая позиция.

В сессии 2026-06-06 сделано (3 PR + 3 деплоя):
1. **Clipboard-fallback AI-черновика** ([#160](https://github.com/Valstan/setka/pull/160)) — кнопка «✨ AI-черновик» в `/notifications` при недоступном Groq копирует готовый промпт в буфер (human-in-the-loop, нулевой бюджет). Заодно: реконсиляция PENDING (закрыты Groq-техдолг + дубль «Фаза 3 CRM») + фикс date-бомбы в `test_ad_crm` (хардкод дат выпадал из скользящего окна).
2. **Инфра графика роста подписчиков** ([#161](https://github.com/Valstan/setka/pull/161)) — миграция 031 `community_member_snapshots` + ORM `CommunityMemberSnapshot` + модуль `modules/members_snapshot.py` + суточная beat-таска `collect-member-snapshots-daily` (04:00 MSK): `groups.getById(fields=members_count)` батчами, upsert по `(community_id, snapshot_date)`, маппинг по `abs(vk_id)` (в БД смешанные знаки 788/108).
3. **Фикс async-резолва токена** ([#162](https://github.com/Valstan/setka/pull/162)) — `_pick_parse_token` (sync) внутри event-loop'а сорил RuntimeWarning + откатывался на env-only без cooldown-фильтра; заменён на async `_resolve_parse_token` через `get_active_parse_tokens(session)`. Пойман на живом деплое #161.

## Следующий шаг

Активной нитки нет. Кандидатные стартовые точки (приоритет — за владельцем):

1. **График подписчиков — шаг 3 (UI)**: страница/виджет мульти-line Chart.js + toggle-чекбоксы + API `member-history` поверх `community_member_snapshots`. ⏳ **Делать после накопления ≥ нескольких недель снимков** (сейчас данные — 1 точка/сообщество, график был бы пустой). Снимки копятся автоматически 04:00 MSK.
2. **AI-дедуп новостей** — отложен; путь без бюджета: локальные embeddings (`multilingual-e5`/`LaBSE`, CPU).
3. **Фазы 4/5 рекламного кабинета** (см. `PENDING_FOLLOWUPS.md`): ML-классификатор рекламы за интерфейсом `classifier.classify`; авто-правила ответов / follow-up / аналитика воронки.
4. **Браузер-верификация владельцем** свежих фич: `/notifications` (clipboard-fallback AI-черновика), `/ad-crm` (виджеты прошлой сессии).

## Контекст

- **План:** нет активного файла-плана; roadmap'ы — в `PENDING_FOLLOWUPS.md`.
- **Связанные коммиты сессии (все на проде `33bdf6d`):**
  - `f056dd8`/[#160](https://github.com/Valstan/setka/pull/160) — clipboard-fallback AI-черновика + реконсиляция PENDING + фикс date-бомбы теста.
  - `c279c91`/[#161](https://github.com/Valstan/setka/pull/161) — инфра снимков подписчиков (миграция 031 + таска + beat).
  - `33bdf6d`/[#162](https://github.com/Valstan/setka/pull/162) — async-резолв токена в снимках (без RuntimeWarning, с cooldown).
- **Прод:** HEAD `33bdf6d`, 3/3 active, health 200. Миграция 031 применена. Beat-слот `collect-member-snapshots-daily` активен (первый авто-снимок — 04:00 MSK). ~1021 тест зелёный на main.
- **Открытых PR:** doc-only handoff-PR этого `/close_session` (авто-merge). Кодовых открытых PR нет.

## Открытые вопросы для пользователя

- Следующая нитка: дождаться данных для графика подписчиков и сделать UI, AI-дедуп, фазы 4/5 кабинета, или иное?

## Не забыть (low-priority)

- 🟢 **График подписчиков (шаг 3)** ждёт накопления снимков — проверить через неделю-две, что таска исправно пишет (`SELECT snapshot_date, count(*) FROM community_member_snapshots GROUP BY 1 ORDER BY 1`), затем строить UI.
- ⚠️ **VPS периодически таймаутил SSH** в этой сессии (порт 49237), восстанавливался за ~10-20с — на работу прода не влияло, но при деплое закладывай retry.
- 🟢 **Браузер-верификация владельцем** clipboard-fallback AI-черновика (`/notifications`) — на проде нет `GROQ_API_KEY`, так что кнопка идёт именно через fallback.

---

> Если читаешь это в начале новой сессии — обнови через `/close_session` в конце. **Не аккумулируй history тут** — она в `git log --follow -- docs/SESSION_HANDOFF.md`.
