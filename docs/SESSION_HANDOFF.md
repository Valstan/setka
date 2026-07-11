# Session Handoff

> Sticky-note для непрерывности между сессиями разработки SETKA. Перезаписывается через [`/close_session`](../.claude/commands/close_session.md) — историю смотри через `git log --follow -- docs/SESSION_HANDOFF.md`.

**Status:** ACTIVE (открытые нитки — owner-gated; активной код-задачи нет, открытая стартовая позиция)
**Updated:** 2026-07-11
**Branch:** main
**Last release in prod:** код HEAD **`e7f54d0`** (#333). Прод трогали этой сессией: `git pull` + restart web/worker дважды (#332 AuthGate, #333 discovery/seed) — оба без миграций. Health 200, все сервисы active. **NB:** #334 (deadcode known.txt) в main, но НЕ на проде — деплой не нужен (dev-инструмент).

---

## Текущая нитка

Активной **код**-нитки нет. Нитка HITL-классификатора закрыта по коду прошлой
сессией; эта сессия — серия точечных фиксов из mailbox brain + бэклога, все
смержены. Открытые нитки (VK-шлюз v2 write, отказ от ре-трансляции агрегаторов)
ждут **решений владельца по scope** — сами по себе в работу не берутся.

Сделано этой сессией (всё смержено):
- **#332** — AuthGate `/oidc/authorize` → 302 для не-браузерных GET (был 401),
  запрос trener через brain. `FRONT_CHANNEL_GET_PATHS` в `middleware/auth_gate.py`.
  Задеплоено, смоук зелёный (внутр. + публичный `вход.вмалмыже.рф`).
- **#333** — discovery/seed: не пускать личные VK-профили в community-пул (запрос
  владельца, парный к #330). `_harvest_repost_owner_ids` берёт только
  `owner_id < 0`; сидер `seed_region_communities.py` — best-effort VK-валидация.
  Задеплоено (restart worker), +4 теста.
- **G140-аудит** — «rebuild-по-полям → JSON-колонка» (находка соседа через brain):
  прогнал по всему проекту, **реального риска нет** (везде merge/whole-object).
- **#334** — deadcode-триаж 2026-07: 14 «новых» → 12 устаревшие ключи known.txt
  после рефактора digest→bulletin (#275), 2 реально новых `sleeping`. Doc-only.

## Следующий шаг

Активной код-задачи нет. Кандидаты (по приоритету):

1. **Owner-gated (нужно решение владельца по scope):** (а) VK-шлюз v2 **write**
   (guarded, per-key scope — security-чувствительно; PENDING §«VK-шлюз»);
   (б) отказ от ре-трансляции чужих reklama-агрегаторов (риск бана VK; PENDING §
   «Рекламная рубрика», `is_hard_spam` пока держит).
2. **🟢 Мелочь автономная:** аудит `TextOnlyBulletinBuilder`/`build_bezfoto_bulletin`
   (`bulletin_builder.py`, вердикт test-only) — реально ли bezfoto-путь мёртв в
   проде и можно ли удалить пакетом (сейчас держит только `test_text_utils.py`).
3. **За оператором (не код):** разбор ленты `/classifier` (agree-rate растёт).

## Контекст

- **План:** нет активного.
- **Связанные коммиты сессии:** `5c87d5a` (#332 AuthGate), `e7f54d0` (#333
  discovery/seed leak), `a7a6582` (#334 deadcode known.txt).
- **Прод:** все сервисы active, health 200, HEAD `e7f54d0` (= #333; #334 не деплоен,
  не нужен). Миграций этой сессией не было.
- **Открытых PR:** только закрывающий handoff-PR (doc-only, авто-merge).

## Failed approaches (этой нитки)

- **Не было** новых тупиков — все три фикса легли с первого захода. NB по deadcode:
  «14 новых кандидатов» после рефактора-переименования — это НЕ регрессия, а
  устаревшие ключи `deadcode_known.txt` (grep по СТАРОМУ имени при rename ловит это
  в PR, а не через месяц; находка отправлена в мозг для rename-playbook #056).

## Открытые вопросы для пользователя

- **VK-шлюз v2 write** — какие VK-методы записи разрешить, per-key scope? Пока не решено.
- **Reklama-агрегаторы** — отказываться ли от ре-трансляции чужих агрегаторов
  объявлений целиком (оставить только свои через ad_cabinet)? `is_hard_spam` держит,
  но при повторных банах — пересмотреть.

## Не забыть (low-priority)

- 🟢 2 новых `sleeping` из deadcode (не удалять): `classifier/schema.py::has_merge_signal`
  (для merge agree-rate, не подключён), `radar_id/keys.py::keys_available` (readiness
  RS256-ключа Ф1, не подключён к health-check).
- 🛠 git push на этой машине: HTTP/2+schannel флапает — обход `git -c http.version=HTTP/1.1 push`.
- 🟢 **За владельцем:** живой VK round-trip-смоук Радар-ID (#011) → потом подключаем
  2-го клиента (GONBA/Sabantuy), trener готов служить образцом.

---

> Если читаешь это в начале новой сессии — обнови через `/close_session` в конце. **Не аккумулируй history тут** — она в `git log --follow -- docs/SESSION_HANDOFF.md`.
