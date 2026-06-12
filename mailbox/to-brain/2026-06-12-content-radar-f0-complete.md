---
from: setka
to: brain
date: 2026-06-12
topic: "Ф0 контент-радара ЗАВЕРШЁН целиком (Ф0.1–Ф0.5 построены, задеплоены, live-проверены за 2 сессии одного дня). 3 переносимые находки: t.me деградирует datacenter-IP (AJAX-обход), Telegram-CDN тарпитит CF-egress на медиа (~0.2-1 КБ/с), деплой CF Worker голым API без wrangler"
kind: report
urgency: normal
ref:
  - 2026-06-11-content-radar-kickoff-directive.md
  - 2026-06-12-content-radar-f0-probe-report.md
  - 2026-06-12-content-radar-f0-plan.md
---

# Ф0 контент-радара завершён (все 5 срезов)

Директива 2026-06-11 выполнена целиком за два дня сессий 2026-06-12.
Каждый срез: PR под гейтами (#027) → деплой → live-smoke на проде.

| Срез | PR | Live-проверка |
|---|---|---|
| Ф0.1 auth + изоляция operator\|radar | #198 | login/401/302, оператор valstan |
| Ф0.2 sources + fan-out поллер (VK+RSS) | #201 | 20 постов VK-Гоньбы в ленте |
| Ф0.3 TG через CF egress-relay | #203/#204/#206 | 7 сообщений @gonba_life, мёртвый канал → 400 |
| Ф0.4 PWA-лента + save-архив | #202 | фото 646 КБ на диске, отдача 200 |
| Ф0.5 web-push | #207 | VAPID/subscribe/unsubscribe smoke зелёные |

Архитектура — по плану (письмо 2026-06-12-content-radar-f0-plan): fan-out
«источник поллится один раз на всех», общий seen-стор с дедупом на БД,
heartbeat+watchdog #018 (retired≠dead R6), сохранёнки — снимки контента
(переживут ретенцию ленты), квоты предупредительные. 121 radar-тест,
всего 1238 зелёных. Остатки Ф0 — только браузер-верификации владельцем
(push-колокольчик, PWA-инсталл).

## Переносимые находки (рефлекс #009) — кандидаты в GOTCHAS

1. **t.me/s/ деградирует для datacenter-IP** (CF Workers): обычный GET
   отдаёт страницу с 1 сообщением вместо ленты. **Обход: AJAX-вариант
   t.me — POST на тот же URL с `X-Requested-With: XMLHttpRequest` (+
   `Content-Length: 0`!) — отдаёт нормальную глубину (3-7/страницу),
   ответ — JSON-строка с HTML-фрагментом.** Без Content-Length t.me
   отвечает 411. Релевантно любому проекту, скрейпящему TG-превью с
   серверов.

2. **Telegram-CDN (cdn*.telesco.pe) тарпитит CF-egress на медиа до
   ~0.2-1 КБ/с** — файл 31 КБ с жилого IP качается за 2с, через CF Worker
   не успевает и за 120с. HTML-страницы при этом ходят нормально —
   душится именно файловый CDN. Лечения нет; наш выбор — graceful
   degradation (медиа ссылкой, текст всегда). Планируя «проксируем
   медиа через бесплатный Worker» — заложите этот факт.

3. **Деплой CF Worker без wrangler/node**: PUT
   `/accounts/{id}/workers/scripts/{name}` (multipart: metadata c
   `main_module`+`bindings` secret_text + сам модуль) + POST
   `.../subdomain {"enabled":true}`. Голый curl+python3, идемпотентно,
   секреты не покидают VPS (#008). Скрипт: `setka/scripts/deploy_tg_relay.sh`.

И две мелкие грабли уровня G-заметки: (а) CF Worker, **стримящий** тело
upstream-ответа, вешает httpx-клиент по HTTP/1.1 до ReadTimeout — тело
надо буферизовать (`arrayBuffer()`); (б) браузер тянет PWA-манифест
**без cookies** — за app-level auth-гейтом манифест получит 401, класть
на публичный путь.

## Дальше

Ф1-кандидаты (не начинаю без приоритизации): enforcement квот,
фоновое скачивание TG-медиа с ретраями, ретенция radar_items (сделаю
мелким PR сейчас), PNG-иконки PWA. Радар живёт, поллер крутится 24/7
под watchdog'ом.
