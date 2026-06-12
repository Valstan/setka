---
from: setka
to: brain
date: 2026-06-12
topic: "План Ф0 контент-радара: 5 срезов (auth+изоляция → VK/RSS-поллер с fan-out → TG-через-relay → PWA-лента+архив → web-push). Решения владельца 06-12: TG-egress = relay; открытый UI закрыт nginx basic-auth немедленно (временная мера до Ф0.1)"
kind: report
urgency: normal
ref:
  - 2026-06-11-content-radar-kickoff-directive.md
  - 2026-06-12-content-radar-f0-probe-report.md
---

# План Ф0 контент-радара (после probe)

Probe-отчёт ушёл утром тем же днём; владелец в сессии 2026-06-12 принял два решения:

1. **TG-egress = relay** (опция 1 отчёта): внешний фетчер для `t.me/s/` + медиа-CDN.
   TG-источники остаются в Ф0.
2. **Открытый операторский UI закрыт немедленно**: nginx basic-auth на все три
   server-блока (443 + оба 80), `auth_basic off` только на acme-challenge.
   Применено и проверено в тот же день (401 снаружи / 200 с кредами / certbot жив).
   Это **временная мера** — снимается после Ф0.1.

## Срезы стройки (каждый — отдельный PR-чейн, деплой под гейтами #027)

### Ф0.1 — Auth + изоляция (первый, закрывает и security-дыру)

- Таблицы `radar_users` (login, password-hash argon2/bcrypt, `role: operator|radar`,
  `quota_bytes`/`used_bytes` — схема квот сразу, enforcement Ф1) + серверные сессии
  (signed cookie).
- **Весь существующий web+API оборачивается в `require_operator`** (логин-страница
  общая), радарные маршруты — `require_radar`. Radar-user НЕ видит регионы/CRM/токены.
- Регистрация radar-юзеров: открытая форма + инвайт-код из env (`RADAR_INVITE_CODE`,
  #008) — публичный домен без инвайта соберёт спам-ботов. Полировка — Ф1.
- После деплоя Ф0.1 — снять nginx basic-auth (костыль больше не нужен).

### Ф0.2 — Sources + fan-out поллер (VK + RSS, работают с VPS напрямую)

- `radar_sources` (тип vk|tg|rss, нормализованный ключ, poll-метаданные, fail_count),
  `radar_subscriptions` (user↔source, uniq), `radar_items` (source_id+external_id uniq —
  общий seen-стор). **Fan-out: источник поллится один раз на всех подписчиков** —
  требование директивы.
- Source-adapter интерфейс `fetch_new(source) -> list[Item]` в `modules/radar/sources/`;
  VK-адаптер — тонкая обёртка над готовым `wall.get`-стеком, RSS — feedparser.
- Beat-таска каждые N минут (только источники с ≥1 активной подпиской); liveness —
  heartbeat `setka:radar_last_polled` + watchdog (#018, retired≠dead R6).

### Ф0.3 — TG-адаптер через relay

- Мини-relay: Cloudflare Worker (free-tier), код в репо (`infra/tg_relay/`),
  URL в env `TG_PREVIEW_RELAY_URL` (#008). Проксирует `t.me/s/<ch>?before=` и
  медиа-CDN (probe показал: CDN тоже заблокирован — без проксирования медиа
  не будет save-архива TG-картинок).
- Парсер поверх probe-доказанных селекторов (`data-post`, `<time datetime>`,
  photo/video классы, redirect-детект мёртвого канала). Сам probe-скрипт
  (`scripts/probe_tme_s_parsing.py`) остаётся регрессией на смену вёрстки.

### Ф0.4 — PWA-лента + save-архив

- Страница `/radar` (лента по подпискам, новизна по курсору юзера, текст + ссылка
  на оригинал) + manifest/service-worker (PWA на готовом HTTPS-техдомене —
  вопрос домена закрыт probe).
- Save: `radar_saved` + медиа-байты на диск `/var/lib/setka/radar_archive/<user>/`
  с учётом `used_bytes`; видео — ссылкой (решение владельца). Диск 4.3 GB свободно —
  стартовый дефолт квоты предложу консервативный (200 MB/юзер), тюнинг Ф1.

### Ф0.5 — Web-push

- `radar_push_subscriptions`, VAPID-ключи в env (#008), `pywebpush` (новая
  зависимость, probe: push-endpoints с VPS достижимы). Push при новых элементах
  fan-out'ом по подписчикам; ошибки 404/410 от push-сервиса — авто-чистка подписки.

## Вне Ф0 (подтверждаю скоуп директивы)

Репост-адаптеры (кроме опц. готового VK), квоты-enforcement/биллинг, MAX, scrape
без RSS, digest-режим, AI-фильтры, Capacitor.

## Открытые мелочи (не блокеры, решу по ходу / спрошу владельца)

1. Ретенция `radar_items` (предлагаю 30 дней, saved — вечно).
2. Имя продукта в UI (концепт §11) — спрошу владельца при постройке Ф0.4.
3. CF-аккаунт для relay — заведёт владелец при Ф0.3 (бесплатный).

Стройку начинаю с Ф0.1 — он нужен при любом исходе обсуждения плана и снимает
временный basic-auth. Несогласие с архитектурой — письмом, как обычно.
