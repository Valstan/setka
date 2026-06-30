# Session Handoff

> Sticky-note для непрерывности между сессиями разработки SETKA. Перезаписывается через [`/close_session`](../.claude/commands/close_session.md) — историю смотри через `git log --follow -- docs/SESSION_HANDOFF.md`.

**Status:** IDLE (3 нитки сессии закрыты/задизайнены; mid-flight кода нет)
**Updated:** 2026-06-30
**Branch:** main
**Last release in prod:** прод **код** HEAD `e3bc416` (#291) — в этой сессии код НЕ деплоился. **Миграция 050 применена напрямую (psql) 2026-06-30** под гейтом #025 (59 dead → `is_active=false`); прод git HEAD при этом не двигался — миграция-файл подтянется при следующем `git pull`/`/reliz`, **повторное применение идемпотентно** (guard `WHERE is_active=true AND health_status='dead'`). Расхождение main↔прод — всё doc-only/no-deploy (#292 MCP-обёртка + #294–#297 доки).

---

## Текущая нитка

_Нет mid-flight кода — открытая стартовая позиция._ За сессию обработаны 3 нитки из mailbox brain
2026-06-30 (все merged): **(1)** LLM-курация свёрнута (ветка B); **(2)** Discovery — 59 dead вынесены
+ политика dormant; **(3)** Радар-SSO — дизайн + контракт (design-first, постройки нет).

Доминирующая нитка на будущее — **Радар-ID (OIDC-SSO)**: дизайн готов (ADR-0002), контракт отправлен
brain, ответы владельца получены. **Ждёт ратификации контракта brain** (внешнее) → затем постройка Ф1.

## Следующий шаг

Активного mid-flight нет. Кандидатные стартовые точки (по приоритету):

1. **Радар-ID Ф1 — после ратификации контракта brain.** Когда brain подтвердит OIDC-контракт
   (issuer/claims/jwks/PKCE) — заходить в постройку: миграция (расширить `RadarUser`: `sub`
   opaque/email/email_verified/соц-id + 3 oauth-таблицы) + Authlib-ядро (discovery/jwks/authorize/
   token/userinfo) + локальный логин + ВК-upstream (R16) + регистрация клиента **trener** + smoke
   round-trip (#011). Issuer **`вход.вмалмыже.рф`** (punycode `xn--b1ae3a1a.xn--80adkdyec4j.xn--p1ai`).
   Полный дизайн — `docs/adr/0002-radar-sso-oidc-provider.md`. **Код клиентов не писать, пока контракт
   движется.** TLS на хосте setka — решить при деплое (новая публичная поверхность).
2. **Discovery dormant-политика — после OK brain.** Tiered по возрасту `last_post_at` (T1>12мес kill /
   T2 6-12мес watch / T3 60д-6мес keep / empty_wall re-probe) отправлена brain; до OK массово dormant
   НЕ трогать. См. PENDING «🧹 Discovery».
3. **VK-шлюз v2 — запись в VK (guarded).** Security-чувствительно: нужны scope-решения владельца перед
   постройкой (probe + дизайн). См. PENDING «🌐 VK-шлюз → v2-бэклог».
4. Прочее из PENDING: Кругозор (2×-эксперимент за владельцем), генератор обложек (ждёт фон brain↔владельца).

## Контекст

- **План:** нет активного плана (Радар-SSO дизайн — в ADR-0002, не в `docs/plans/`).
- **Связанные коммиты сессии:**
  - `2c4394f` (#294) — LLM-курация свёрнута (ветка B), shadow-таблица сохранена (doc-only).
  - `91bdd5f` (#295) — Discovery: миграция 050 (59 dead) + политика dormant для brain.
  - `1a090b2` (#296) — Радар-SSO дизайн: ADR-0002 + контракт brain.
  - `4129c86` (#297) — Радар-SSO: решения владельца (нейминг, домен, пилот trener).
- **Прод:** все сервисы active, health 200 (probe начала сессии), код HEAD `e3bc416`. БД: миграция 050
  применена (59 dead disabled, verified 0 dead+active; осталось 773 active + 98 dormant). Миграция 049 — прошлая сессия.
- **Открытых PR:** этот handoff-PR (doc-only, авто-merge). #294–297 смержены.

## Открытые вопросы для пользователя

- **Радар-SSO:** ждём ратификации контракта brain — после неё стартуем Ф1 (trener). Пинговать brain не
  нужно (контракт уже у него письмами; ответит своим `git pull`).

## Не забыть (low-priority)

- 📬 **Письма-находки сессии** (уедут к brain его `git pull`): `2026-06-30-curation-wind-down-ack.md`,
  `2026-06-30-discovery-dead-dormant-cleanup.md`, `2026-06-30-radar-sso-contract.md`,
  `2026-06-30-radar-sso-owner-answers.md`.
- 🟢 **Discovery находки → бэклог:** (а) dead-ведро = в основном ошибочно добавленные ЛИЧНЫЕ профили VK
  (просечка seed/discovery — пускали профили в community-пул); (б) recheck не персистит `error_code` →
  предложен апгрейд для разделения dead 18/100 vs недостижимо 15/203 (РКН). См. PENDING «🧹 Discovery».
- 🟢 **ADR-0002 §4 нит:** в таблице остался иллюстративный `id.малмыже.рф/auth/vk/callback` —
  канонический issuer `вход.вмалмыже.рф`. Поправить одной строкой при следующем касании Радар-доков.
- 🛠 **git push на этой машине:** HTTP/2+schannel периодически висит (`SSL/TLS handshake`) — рабочий
  обход `git -c http.version=HTTP/1.1 push`. `gh` API тоже флапал (TLS timeout) — ретраить.
- 🟢 **Раздать `GATEWAY_KEY_*` потребителям** + `gateway_mcp/` (готова): малмыж-сайты / GONBA (brain #062).
- 🟢 Браузер-проверки владельцем (физические, не блокеры) — см. PENDING «Пакет браузер-верификаций».
- 🟢 Следующий dead-code прогон ~2026-07-14.

---

> Если читаешь это в начале новой сессии — обнови через `/close_session` в конце. **Не аккумулируй history тут** — она в `git log --follow -- docs/SESSION_HANDOFF.md`.
