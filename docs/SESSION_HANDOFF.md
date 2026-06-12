# Session Handoff

> Sticky-note для непрерывности между сессиями разработки SETKA. Перезаписывается через [`/close_session`](../.claude/commands/close_session.md) — историю смотри через `git log --follow -- docs/SESSION_HANDOFF.md`.

**Status:** ACTIVE (наблюдение — кодовых ниток нет, ближайшее действие календарное)
**Updated:** 2026-06-13
**Branch:** main
**Last release in prod:** прод на `e71d0a1` = main (все PR сессии #201–#215 задеплоены, 3/3 active, health 200).

---

## Текущая нитка

**Контент-радар Ф0 ЗАВЕРШЁН ЦЕЛИКОМ** (MANDATE brain 2026-06-11): за сессию построены/задеплоены/live-проверены Ф0.2 (sources + fan-out поллер VK+RSS, PR #201), Ф0.4 (PWA-лента + save-архив, #202), Ф0.3 (TG через CF egress-relay `tg-relay.zubazeirot.workers.dev`, #203/#204/#206), Ф0.5 (web-push, #207), ретенция ленты (#208), PNG-иконки (#209). Отчёт мозгу с 3 переносимыми находками — `mailbox/to-brain/2026-06-12-content-radar-f0-complete.md`.

Сверх радара: ответ brain'у на #037 filestore-race («риска нет», #210) и **разбор первого deadcode-триажа целиком** — 4 пакетных PR #211–#214 (carousel-цепочка, старые publisher'ы, postopus-core, россыпь utils; −1000+ строк, orphan-цепочки #028 прослежены).

Нитки в наблюдении: PoC LLM-курации `mi` (цифры ~2026-06-14), браузер-верификации владельцем (пакет в PENDING, + радар-PWA/колокольчик).

## Следующий шаг

1. **~2026-06-14: цифры PoC курации** — `ssh setka "cd /home/valstan/SETKA && sudo bash -c 'set -a; source /etc/setka/setka.env; set +a; ./venv/bin/python scripts/curate_pending.py --stats'"` → ack brain письмом в `mailbox/to-brain/` (он ждёт; решение Фазы 2: enforcing fail-open vs Haiku-API).
2. Проверить mailbox — brain мог ответить на Ф0-complete отчёт и #037 (`cd ../brain_matrica && git pull --ff-only`).
3. Если владелец прошёл браузер-верификации — вычеркнуть пакет из PENDING.

## Контекст

- **План:** активного плана нет; roadmap Ф1-кандидатов радара — в отчёте `mailbox/to-brain/2026-06-12-content-radar-f0-complete.md` (квоты-enforcement, фоновое TG-медиа, не начинать без приоритизации).
- **Связанные коммиты сессии:** `7a4684f` #201 Ф0.2, `ecb9b84` #202 Ф0.4, `3b7eedf` #203 Ф0.3, #204/#206 relay-фиксы, `558f5e8` #207 Ф0.5, #208 отчёт+ретенция, #209 PNG-иконки, #210 ответ #037, #211–#214 deadcode, `e71d0a1` #215 PENDING.
- **Прод:** HEAD `e71d0a1` = main, 4/4 active (вкл. nginx), health 200. Радар-поллер крутится `*/10` под watchdog'ом; CF-relay жив (`https://tg-relay.zubazeirot.workers.dev/health`). Secrets в `/etc/setka/setka.env`: + `CLOUDFLARE_API_TOKEN`, `TG_RELAY_SECRET`, `TG_PREVIEW_RELAY_URL`, `RADAR_VAPID_PRIVATE_KEY`, `RADAR_VAPID_SUBJECT`. `/etc/setka/web_basic_auth.txt` удалён.
- **Открытых PR:** нет (handoff-PR этой сессии — doc-only, авто-merge).

## Failed approaches (этой нитки)

- **Стриминг тела из CF Worker** (`return new Response(resp.body)`) — вешает httpx-клиент по HTTP/1.1 до ReadTimeout. Не повторять: тело буферизовать `arrayBuffer()` (PR #204).
- **Скачивание TG-медиа через CF-relay с большим таймаутом** — бесполезно: Telegram-CDN душит CF-egress до ~0.2-1 КБ/с (файл 31 КБ не проходит и за 120с). Принято graceful degradation: фото ссылкой, попытка 20с (PR #206). Не наращивать таймаут — лечится только другим egress / фоновым скачиванием (Ф1).
- **Обычный GET к t.me/s/ из CF** — отдаёт 1 сообщение (деградация для datacenter-IP). Рабочий путь: AJAX POST + `X-Requested-With` + `Content-Length: 0` (без него t.me отвечает 411).

## Открытые вопросы для пользователя

- Браузер-верификации (один ~20-мин проход, чек-лист в PENDING): радар-PWA на телефоне (иконка, колокольчик-push), tiered-поиск, ad-cabinet/CRM.
- Ф1 радара: что приоритетнее — квоты-enforcement, фоновое TG-медиа, или пауза до фидбэка от использования?

## Не забыть (low-priority)

- 🟢 `utils/post_utils.py::format_number` остался без потребителей после deadcode-пакета 4 — снимет месячный прогон `/deadcode` (~2026-07-10).
- 🟢 Pillow поставлен только в локальный venv (генерация PNG-иконок, `scripts/generate_radar_icons.py`) — в requirements не входит, это норма.
- ⏸ AI-дедуп (embeddings) — `parked` до VPS ≥4 ГБ.

---

> Если читаешь это в начале новой сессии — обнови через `/close_session` в конце. **Не аккумулируй history тут** — она в `git log --follow -- docs/SESSION_HANDOFF.md`.
