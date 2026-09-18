# 📡 Контент-радар — Ф1 (приоритизация brain 2026-06-13) — ЗАКРЫТО

> Запись `P047` реестра [PENDING_FOLLOWUPS](../PENDING_FOLLOWUPS.md). Индекс грепается, запись читается целиком, реестр целиком не читается никогда ([D-097](../../AGENTS.md)).

`⏱ 2026-06-13 · snooze 0 · parked` — **ре-триаж 2026-07-27 (pool #033):** секция сама себя объявляет
закрытой в последнем абзаце (все 4 среза ✅), но тег остался `fresh` и она месяц всплывала как
открытая. Единственная живая развилка — **residential-egress прокси** для TG-медиа, и она уже
`parked` по решению владельца («медиа не критично», в браузере CDN-ссылка грузится). Условие
расконсервации: **владелец попросил файлы медиа в архиве**.

Порядок Ф1 утверждён brain ([`mailbox`-письмо `2026-06-13-content-radar-f1-prioritization.md`](../../../brain_matrica/mailboxes/setka/from-brain/2026-06-13-content-radar-f1-prioritization.md)):
ретенция → **enforcement квот** → PNG-иконки → TG-медиа (после probe). Тактика — моя под гейтами #027.

- ✅ **Ф1.1 — ретенция `radar_items`** — уже закрыта 2026-06-12 (beat `radar-items-retention-daily` 03:20,
  `RADAR_ITEMS_RETENTION_DAYS`=30; см. Ф0.4-хвост выше).
- ✅ **Ф1.2 — enforcement квот архива** (операционный риск №1 по brain: tiny-бокс + «вечный» архив →
  переполнение диска убьёт поллер/Postgres). **Построено + задеплоено 2026-06-13** ([PR #224](https://github.com/Valstan/setka/pull/224),
  прод HEAD `1349055`, restart web, health 200) (ветка `feat/radar-quota-enforcement`):
  квоты перестали быть только предупредительными. `modules/radar/archive.py` — порог свободного места на
  диске `RADAR_ARCHIVE_MIN_FREE_BYTES` (дефолт 2 ГиБ): `download_media` не пишет фото, если запись опустит
  свободное место ниже порога (защита всего 10-ГБ бокса, не только per-user). `web/api/radar.py` —
  глобальный потолок суммарного архива всех юзеров `RADAR_ARCHIVE_MAX_BYTES` (дефолт 2 ГиБ): `save_item`
  считает `SUM(used_bytes)` и режет `quota_left = min(per-user, global)`. Оба degrade-to-link (текст всегда
  сохраняется — решение владельца). `list_saved` отдаёт box-level статус (`archive.writable`), UI на `/radar`
  показывает «архив заполнен — новые фото ссылкой». Без миграции. +11 тестов (1288 зелёных).
  **Деплой:** restart web (env-дефолты работают без настройки; владелец может ужесточить порог env'ом).
  Прод-факт 2026-06-13: диск 10.6 ГБ, свободно 4.56 ГБ, архив радара 646 КБ.
- ✅ **Ф1.3 — PNG-иконки PWA** (quick win) — **уже закрыто в Ф0.4, подтверждено 2026-06-13**:
  `icon-192/512.png` (10/27.6 КБ) + `manifest.webmanifest` install-ready (`display: standalone`, иконки
  192/512 `any maskable` + SVG, scope/start_url `/radar`), apple-touch-icon в `radar.html`. Работы не нужно.
- ✅ **Ф1.4 — TG-медиа: probe закрыл как нежизнеспособное** (probe-before-build #020 сэкономил мёртвый
  воркер). **Probe 2026-06-13 с прод-бокса:** 20 cdn-URL'ов @gonba_life через relay → 10 прямых скачиваний
  (не через CF) → **0/10, все `ConnectError: All connection attempts failed` за ~7.8с.** Это **не G56-тарпит,
  а hard-block на connection-level** — бокс вообще не открывает TCP к `*.telesco.pe`. Воркер с ретраями
  бесполезен (сквозь refused-коннект не доретраишься). Развилка: **(a) принять text+link навсегда** ✅
  (рекомендовано brain'у; владелец: медиа «не критично»; в браузере юзера CDN-ссылка грузится) / (b)
  residential-egress прокси — `parked` до явного запроса «файлы медиа в архиве» / (c) всё через relay —
  нежизнеспособно (G56). Отчёт — `mailbox/to-brain/2026-06-13-radar-tg-media-probe-result.md`.

**Радар-Ф1 закрыт целиком** (1 ретенция ✅ · 2 квоты ✅ задеплоено · 3 PNG-иконки ✅ были в Ф0.4 ·
4 TG-медиа ✅ probe закрыл как нежизнеспособное). Открытая развилка — только residential-egress (b), `parked`.
