# Session Handoff

> Sticky-note для непрерывности между сессиями разработки SETKA. Перезаписывается через [`/close_session`](../.claude/commands/close_session.md) — историю смотри через `git log --follow -- docs/SESSION_HANDOFF.md`.

**Status:** ACTIVE
**Updated:** 2026-06-12
**Branch:** main
**Last release in prod:** прод на `649adfa` (Ф0.1 auth задеплоен: миграция 037 применена, оператор `valstan` создан, временный nginx basic-auth снят).

---

## Текущая нитка

**Контент-радар Ф0** (MANDATE-директива brain 2026-06-11). За сессию пройден полный цикл по порядку директивы: probe → план → стройка первого среза → деплой:

1. **Probe #020** ([PR #196](https://github.com/Valstan/setka/pull/196)): `t.me/s/` механика работает, но **с VPS заблокирован весь Telegram кроме `api.telegram.org`** (включая медиа-CDN) → решение владельца: **egress-relay** (CF Worker, Ф0.3). Web-push зелёный. HTTPS-техдомен `3931b3fe50ab.vps.myjino.ru` уже жив (wildcard LE jino). ⚠️ Вне плана: операторский UI был открыт в интернет без auth (вкл. `/tokens`).
2. **План Ф0 письмом brain'у** ([PR #197](https://github.com/Valstan/setka/pull/197)): срезы Ф0.1 auth → Ф0.2 sources+fan-out поллер (VK+RSS) → Ф0.3 TG-relay → Ф0.4 PWA-лента+архив → Ф0.5 web-push.
3. **Ф0.1 построен и задеплоен** ([PR #198](https://github.com/Valstan/setka/pull/198)/[#199](https://github.com/Valstan/setka/pull/199)): миграция 037 `radar_users`, scrypt+signed-cookie (всё stdlib), `middleware/auth_gate.py` secure-by-default, изоляция operator|radar, `/login`+`/radar`. Временный nginx basic-auth снят после проверки. Внешний smoke: браузер→302 /login, API→401, login→200+cookie+дашборд.

Нитки в наблюдении (без изменений): PoC LLM-курации `mi` (цифры ~2026-06-14), tiered-поиск #035 (браузер-верификация), near-dup Jaccard (мониторинг логов).

## Следующий шаг

1. **Ф0.2 — sources + fan-out поллер (VK+RSS):** таблицы `radar_sources`/`radar_subscriptions`/`radar_items` (uniq `source_id+external_id`), adapter-интерфейс `fetch_new(source)` в `modules/radar/sources/`, beat-таска + heartbeat #018. Дизайн — в плане `mailbox/to-brain/2026-06-12-content-radar-f0-plan.md`.
2. **~2026-06-14: цифры PoC курации** — `ssh setka "cd /home/valstan/SETKA && ./venv/bin/python scripts/curate_pending.py --stats"` → ack brain (он ждёт).
3. Проверить mailbox — brain мог ответить на probe-отчёт/план (`cd ../brain_matrica && git pull --ff-only`).

## Контекст

- **План:** `mailbox/to-brain/2026-06-12-content-radar-f0-plan.md` (срезы Ф0.1–Ф0.5); roadmap — `PENDING_FOLLOWUPS.md` (секция «Контент-радар Ф0»).
- **Связанные коммиты сессии:** `b8a733f` #196 probe, `bf643d0` #197 план, `649adfa` #198 Ф0.1, `633fb51` #199 deploy-status.
- **Прод:** HEAD `649adfa`, 4/4 active (incl. nginx), health 200, миграция 037 применена. Auth живой: оператор `valstan`, пароль root-only в `/etc/setka/web_operator_credentials.txt` (либо владелец сменил его через `scripts/create_radar_user.py` — команда давалась в конце сессии). `SETKA_WEB_SECRET` в `/etc/setka/setka.env`. Бэкап nginx-конфигов: `/root/nginx-backup-20260612/`.
- **Открытых PR:** нет (handoff-PR этой сессии — doc-only, авто-merge).

## Failed approaches (этой нитки)

- _Не было._ (Нюанс: пользователь сообщал «пароля нету / логин не сохраняется» — диагностика показала, что файл и полный login-цикл рабочие (login 200 + cookie + дашборд 200 снаружи); вероятно, вводился старый basic-auth пароль. Дан рецепт смены пароля через `ssh -t … create_radar_user.py`. Если жалоба повторится — смотреть текст ошибки на форме.)

## Открытые вопросы для пользователя

- Получилось ли войти в UI после смены/чтения пароля? (если нет — нужен текст ошибки с формы).
- Для Ф0.3 понадобится бесплатный Cloudflare-аккаунт под TG-relay.

## Не забыть (low-priority)

- 🟢 Браузер-верификации владельцем — чек-лист в PENDING (+ новый пункт: вход на `https://3931b3fe50ab.vps.myjino.ru/`).
- 🟢 `/deadcode` — следующий прогон ~2026-07-10.
- ⏸ AI-дедуп (embeddings) — `parked` до VPS ≥4 ГБ.
- 🟢 `/etc/setka/web_basic_auth.txt` на проде больше не нужен (basic-auth снят) — можно удалить при случае.

---

> Если читаешь это в начале новой сессии — обнови через `/close_session` в конце. **Не аккумулируй history тут** — она в `git log --follow -- docs/SESSION_HANDOFF.md`.
