# Pending follow-ups

Открытые задачи, техдолги и идеи проекта SETKA. **Свежее сверху.**

**Приоритеты:**
- 🔴 **блокер** — прод сломан / нельзя двигаться дальше / безопасность
- ⏳ **в процессе** — начато, не дозавершено
- 🟡 **техдолг** — работает, но «костыль» / непрозрачно / повторение боли
- 🟢 **идея** — улучшение качества жизни, не критично

**Метки старения** ([pool #033](../../brain_matrica/cross-project-ideas/ideas/033-deferred-backlog-aging-retriage.md), директива brain 2026-06-09): у **открытых** пунктов — компактный тег `⏱ YYYY-MM-DD · snooze N · статус`, где дата = когда добавлено, snooze = сколько раз сознательно отложено, статус:
- `fresh` — < 14 дней;
- `watch` — 14–30 дней или ждёт известного внешнего события (указать какого);
- `stale` — > 30 дней или snooze ≥ 3 → **обязан всплыть на `/start`** с ре-триажем тремя исходами: возобновить / переформулировать под текущий код / выкинуть (с причиной);
- `parked` — сознательно отложено до явного условия (указать условие); не считается гниющим, пока условие не наступило.

Исторические закрытые (~~strikethrough~~) записи тегами не размечаем. У пунктов без тега возраст оценивается по датам в тексте.

При закрытии — описательный commit message и/или PR description заменяют старую запись в DEV_HISTORY ([ADR-0001](adr/0001-archive-dev-history.md)). В этом файле — пометь строку `~~strikethrough~~` с короткой ссылкой «закрыто в PR #N» или просто удали. Деталей не хранить — они в `git log` + `gh pr view <N>`. Исторические ссылки на `DEV_HISTORY.md` ниже не правим — они указывают на снимки в `git show HEAD:docs/DEV_HISTORY.md` соответствующего периода.

---

## 🔴 Блокеры

_Сейчас нет._

- ~~**VK-токен VALSTAN не имеет scope `wall`/`likes`**~~ Закрыто 2026-05-26 (этот PR): попытка получить токен с `wall`+`groups` через четыре разных способа провалилась — VK 2026 (а) у публичных mobile-app_id (Kate Mobile, VK Messenger, VK Mobile) либо режет scope (отдаёт `[photos, email, ads, offline]`), либо привязывает токен к IP-адресу выпуска (error 5 `access_token was given to another ip address` при обращении с прод-VPS); (б) для своего Standalone-приложения VK закрыл новую форму создания (на dev.vk.com доступны только Мини-приложение / Игра / Плагин для сообществ), legacy URL `vk.com/editapp?act=create` тоже больше не показывает Standalone; (в) `likes.add` через community-token VK явно отказывается обслуживать с error 27 `Group authorization failed: method is unavailable with group auth`. **Решение**: кнопка ♥ в `/notifications` теперь — обычная ссылка-deeplink `https://vk.com/wall{owner}_{post}?reply={cid}&thread={cid}`, открывает пост в VK с фокусом на комменте, лайк ставится руками в VK. Backend endpoint `/api/notifications/comments/like` оставлен в коде на случай если когда-нибудь scope `wall` снова станет доступен для физлиц.
- ~~**Discovery trigger длится >180s — nginx обрывает клиента**~~ Закрыто 2026-05-25 ([PR #49](https://github.com/Valstan/setka/pull/49), `0edf84b`): trigger переведён на Celery + UI polling через `/api/discovery/task/{id}/status`. UI больше не виснет. Nginx полу-фикс 600s в `/etc/nginx/conf.d/setka.conf` остался — не мешает, можно при желании откатить на 180s.
- ~~**Groq API key возвращает 403 Forbidden**~~ Переведено в 🟡 техдолг 2026-05-26: discovery больше не зависит от Groq (PR #41 AI-batch через clipboard, PR #51 info-repost). Затрагивает только UX-фичу — AI-черновик ответа на VK-комменты в `modules/notifications/ai_drafter.py` (модератор пишет вручную). См. 🟡 ниже.

---

## ⏳ В процессе

### 🔐 Радар-ID — OIDC-провайдер идентичности экосистемы (решение владельца через brain 2026-06-30)

`⏱ 2026-06-30 · snooze 0 · fresh · Ф1 ПОСТРОЕНА и ЗАДЕПЛОЕНА 2026-07-05 (вход.вмалмыже.рф live); остаток — round-trip с trener + Ф2/Ф3`

3 письма brain 2026-06-30 (`radar-as-ecosystem-sso-center`, `radar-auth-vk-arch-unified-login`,
`radar-sso-contract-from-trener`): «Радар» (модуль setka) = единый OIDC-центр всей экосистемы (GONBA,
Sabantuy, малмыж×3, trener, будущие футбол/такси). trener — клиент №1. Канон — `unified-auth-concept.md`
§«Пересмотр 06-30» + Часть A.

- ✅ **Дизайн готов — ADR-0002** (`docs/adr/0002-radar-sso-oidc-provider.md`): Радар-ID = multi-client
  OIDC-провайдер на **Authlib** (крипта библиотекой, не руками). Модель (расширить `RadarUser`: `sub`
  opaque/email/email_verified/соц-id + 3 oauth-таблицы), эндпоинты (discovery/jwks/authorize/token/
  userinfo), claims/scopes per-client, MUST-митигаторы (офлайн-JWKS, RS256+ротация refresh+reuse-detect,
  rate-limit, audit), фазы Ф1–Ф3.
- ✅ **Контракт отправлен brain** (`mailbox/to-brain/2026-06-30-radar-sso-contract.md`): issuer/discovery,
  claims sub/email/email_verified/name, RS256+JWKS, Auth Code+PKCE, ручная client-reg. + рекомендация ВК
  (**одно приложение на слое Радара**, Вариант А уточнённый) + 4-методная login-страница (в Радаре, сайты —
  тонкая кнопка-redirect; R16 ВК + R12 magic-link + Telegram-HMAC).
- ✅ **Решения владельца получены 2026-06-30:** (1) нейминг — **Радар-ID** (SSO) / **Радар-лента** (контент)
  под зонтиком «Радар = платформа Сарафан»; (2) публичный домен **`вход.вмалмыже.рф`** (issuer; punycode
  `xn--b1ae3a1a.xn--80adkdyec4j.xn--p1ai` для ВК-приложения/redirect_uri — G108/R16) + TLS на хосте setka;
  (3) пилотный клиент Ф1 — **trener**.
- ✅ **Контракт РАТИФИЦИРОВАН brain** (`from-brain/2026-06-30-radar-sso-contract-ratified.md`, прочитано
  2026-07-05): как есть, без правок. Brain форварднёт trener сигнал строить свою сторону. 4 MUST-митигатора
  (офлайн-JWKS / короткие access + refresh-ротация + reuse-detect / rate-limit / audit) — условие go-live.
- ✅ **Ф1 ступень 1 — схема (PR #301, 2026-07-05):** миграция 052 (radar_users → аккаунт-слой: sub UUID
  opaque + backfill, email/email_verified, соц-id, login/password nullable; oauth_clients / auth_codes /
  refresh_tokens с family_id), модели, RS256-ключи (`modules/radar_id/keys.py`, ключ файлом
  `/etc/setka/radar_id_rs256.pem`, генератор `scripts/generate_radar_id_key.py`), config issuer punycode.
- ✅ **Ф1 ступень 2 — OIDC-ядро (PR #302, 2026-07-05):** discovery/jwks/authorize/token/userinfo;
  Code + PKCE S256, single-use код, RS256 id_token/access (claims-минимизация по scope), refresh-ротация
  + family reuse-detect, client-auth basic/post/none, rate-limit per-IP, audit-логгер, kill-switch
  `RADAR_ID_DISABLED`; consent auto-approve (все клиенты first-party, ручная регистрация);
  `scripts/register_oidc_client.py`. Локальный логин = существующий /login (сессия RadarUser). 1537 тестов.
- ✅ **Ф1 ступень 3 — ВК-upstream (R16) (PR #304, 2026-07-05):** VK ID OAuth (id.vk.ru, Code+PKCE,
  `device_id` из callback обязателен) как upstream-метод логина Радара. `modules/radar_id/vk_upstream.py`
  + `/auth/vk/login|callback`; связывание по verified-email (анти-захват) / соц-only RadarUser. ВК-приложение
  владельца **«Войти в Сервисы Малмыжа» App ID `54666252`** (Вариант А — одно на слое Радара). 1551 тест.
- ✅ **Ф1 ЗАДЕПЛОЕНА на прод 2026-07-05** (под гейтом #025): pip install (Authlib/joserfc/aiosqlite),
  миграция 052 применена, RS256-ключ сгенерирован (`/etc/setka/radar_id_rs256.pem` 0600, kid
  `LoGbwp2W…`), `RADAR_ID_VK_APP_ID=54666252` в env, клиент **trener** зарегистрирован (confidential;
  секрет в root-only `/etc/setka/trener-oidc-credentials.txt`), restart web/worker/beat, health 200.
- ✅ **Публичная экспозиция поднята 2026-07-05** (через панель Джино, с владельцем): поддомен
  `вход.вмалмыже.рф` → VPS СЕТКА (A-запись + привязка); Let's Encrypt (авто-продление Джино, TLS
  терминируется на edge-прокси Джино — **не** certbot на боксе). nginx server-block
  `/etc/nginx/conf.d/radar_id.conf` (вне git, прод-правка). **Внешний smoke зелёный:** discovery/jwks/
  login = 200; authorize→login redirect с сохранением query; vk-login → 302 на id.vk.ru + PKCE + App ID.
- 🟢 **Остаток Ф1:** (1) round-trip-smoke с trener (#011) — владелец передаёт trener client_secret из
  root-файла + issuer; когда trener построит свою сторону → пинг brain, подключит GONBA/Sabantuy.
  (2) владельцу — физически проверить `https://вход.вмалмыже.рф/auth/vk/login` (вход через ВК → /radar).
  (3) RS256-ключ подписи — кандидат №1 на зеркало в Карман (ADR-0006), когда KARMAN даст mirror-API.
  (4) нит: nginx-блок Радара — ACME-location через webroot оставлен, но реально TLS на edge Джино;
  можно упростить при следующем касании (не блокер).
- 🟢 **Ф2/Ф3 (не сейчас):** magic-link (R12) + Telegram-HMAC + клиент №2 (Ф2); остальные клиенты +
  мобайл-PKCE футбол/такси (Ф3). Когда Сабантуй перейдёт на вход через Радар-ID — его отдельное
  VK ID-приложение (App ID 54656174) выводится, остаётся одно на экосистему (Вариант А, анти-зоопарк).

### 🧹 Discovery — чистка dead + политика dormant (запрос владельца через brain 2026-06-30)

`⏱ 2026-06-30 · snooze 0 · fresh`

Письмо brain `from-brain/2026-06-30-discovery-dead-communities-cleanup.md`: убрать confirmed-dead
сообщества + предложить политику по dormant. Ответ — `mailbox/to-brain/2026-06-30-discovery-dead-dormant-cleanup.md`.

- ✅ **59 confirmed-dead → миграция 050** (`050_disable_dead_communities.sql`, коммичена): обратимый
  soft-disable (`is_active=false`, НЕ DELETE) — выводит из парса (`monitor.py`) и recheck (оба
  фильтруют `is_active=true`), история/FK сохранены, откат в самой миграции. Заземлено на реальных
  данных прода (773 active / 98 dormant / 59 dead). **Находка:** dead-ведро = в основном ошибочно
  добавленные ЛИЧНЫЕ профили VK (+ 2 тест-мусора + закрытые малые паблики), почти нет РКН-недостижимых.
- ✅ **Миграция 050 ПРИМЕНЕНА на проде 2026-06-30** (под гейтом #025, owner-confirm): `UPDATE 59`,
  без restart. Verify: `health_status='dead' AND is_active=true` → **0** (все 59 → is_active=false);
  среди активных осталось 773 active + 98 dormant. Обратимо (откат в миграции).
- ✅ **Политика dormant — ОДОБРЕНА brain (письмо 2026-06-30) и ПОСТРОЕНА 2026-07-05** (этот PR):
  tiered по возрасту `last_post_at` (`classify_dormant_tier`: T1 >12мес / T2 6–12мес watch / T3 60д–6мес
  KEEP / empty_wall re-probe, не авто-kill). Auto-disable **только T1 при 2 подряд dormant** (prev
  `health_status='dormant'` + новый dormant — без новой схемы серий); обратимый soft-disable
  (`is_active=false` + `disabled_at`/`disabled_reason='dormant_t1_auto'`, миграция 051). **Условие brain
  соблюдено:** ежемесячный TG-digest вынесенных (beat `dormant-disable-digest-monthly`, 1-е число 09:30 MSK).
  После первого месячного цикла с верными исходами — оформить «3 судьбы» письмом в brain (#009,
  measure-before-promote). _Деплой: миграция 051 (аддитивная + backfill reason для 59 из 050) + restart
  worker/beat._
- 🟢 **Бэклог:** апгрейд recheck «persist `error_code`» (одно поле) → машинно разделить dead 18/100
  (удалён) от 15/203 (недостижим/РКН — re-probe перед kill, cf #041). Сейчас error_code не хранится.
  Brain 2026-06-30: «разблокирует honest dead/unreachable split» — парный к #041.
- 🟢 **Бэклог (brain 2026-06-30):** закрыть протечку discovery/seed, пускавшую ЛИЧНЫЕ VK-профили в
  community-пул (dead-ведро миграции 050 состояло в основном из них) — валидировать на входе, что
  vk_id принадлежит сообществу, а не пользователю.

### 🌐 VK-шлюз — ворота доступа в VK для других проектов @valstan (запрос владельца 2026-06-26)

`⏱ 2026-06-26 · snooze 0 · fresh`

Другие проекты (и их AI-сессии) просят «сходи в VK — проанализируй сообщество / скачай / импортируй»,
упираясь в закрытость VK 2026. SARAFAN — внутренняя кухня VK (токены + клиент + cooldown + rate-limiter).
Шлюз даёт read-only доступ по HTTP: проект шлёт задачу → SARAFAN исполняет своим токеном → возвращает JSON.
**Токен наружу не выдаётся** (VK привязывает user-токен к IP выпуска → error 5 с чужого бокса).

- ✅ **Построено + ЗЕЛЁНО (v1 read-only):** роутер `/api/gateway` (`web/api/gateway.py`): `POST /call`
  (allowlist read-методов), `GET /community`, `GET /wall`. Auth — API-ключ на проект `GATEWAY_KEY_<PROJECT>`
  (`X-API-Key`, constant-time). Квота на ключ (`modules/gateway/quota.py`, Redis fixed-window, fail-open).
  Переиспользует `TokenPolicy` (cooldown 5/17/29) + `VKClient`. Конфиг/allowlist `config/gateway.py`,
  kill-switch `GATEWAY_DISABLED`. Контракт `docs/GATEWAY.md`. +9 тестов (1472 зелёных), pre-commit чистый.
- ✅ **ЗАДЕПЛОЕНО 2026-06-26** (прод HEAD `f999eb5`): 5 ключей потребителей в `/etc/setka/setka.env`
  (`SABANTUY_MALMYZH`, `VMALMYZHE`, `CDK_KALININO`, `DK_MALMYZH`, `GONBA`), restart web (G92-проверен:
  ActiveEnterTimestamp свежий), health 200. Смоук на проде: no-key→401, valid→`/community`→200,
  `wall.post`→400; внешний HTTPS `3931b3fe50ab.vps.myjino.ru`→401. Домен прописан в `docs/GATEWAY.md`.
- ✅ **Страница статистики (запрос владельца 2026-06-26):** миграция 049 (`gateway_requests`); лог
  запросов `modules/gateway/usage.py` (best-effort: кто/когда/метод/параметры/результат); операторский
  API `/api/gateway-stats` (summary/timeline/recent); страница `/gateway-stats` (таблица по проектам +
  график по дням + лента запросов с параметрами, меню «Система»). Public-prefix ужат `/api/gateway` →
  `/api/gateway/`, чтобы статистика осталась под операторской сессией. +6 тестов (1478 зелёных).
- ✅ **Статистика ЗАДЕПЛОЕНА 2026-06-26** (прод HEAD `2de23ca`): миграция 049 применена (таблица +
  индексы), restart web (G92-проверен), health 200. End-to-end проверено: вызовы `/community` + `/wall` →
  200 и записались в `gateway_requests` (project/method/params/status/duration); `/gateway-stats` под
  операторской сессией (401 без cookie).
- 🟢 _Остаток:_ раздать ключи проектам-потребителям (владелец: `ssh setka "sudo grep '^GATEWAY_KEY_'
  /etc/setka/setka.env"`) + браузер-проверка страницы `/gateway-stats` владельцем.
- ✅ **v2: observability 401/429 + ретеншн (2026-06-29):** отказы **429** (известный проект) и **401**
  с неверным ключом (проект `(unknown)`; пустой ключ не пишем — шум сканеров) теперь логируются в
  `gateway_requests` и видны на `/gateway-stats`. Beat `prune-gateway-requests-daily` (03:40 MSK) чистит
  строки старше `GATEWAY_REQUESTS_RETENTION_DAYS` (дефолт 90). +6 тестов. Без миграции (схема та же).
- ✅ **v2: MCP-обёртка (2026-06-30):** `gateway_mcp/` — stdio MCP-сервер (FastMCP), даёт AI-сессии
  проекта-потребителя инструменты `vk_get_community`/`vk_get_wall`/`vk_call` поверх read-шлюза. Запускается
  у потребителя (env `SARAFAN_GATEWAY_KEY`); в зависимостях SARAFAN `mcp` нет — ядро `client.py` (httpx)
  отделено от `server.py` и покрыто тестами (+20). README + секция в `docs/GATEWAY.md`. Brain #062:
  готово к раздаче малмыжским сайтам/GONBA.
- 🟢 **v2-бэклог (осталось):** запись в VK (guarded, per-key scope; **security-чувствительно** — нужны
  решения владельца по scope перед постройкой); async-джоба для тяжёлого «прочесать весь паблик» (как
  `/api/parsing`).

### 🔭 Поток «Кругозор» — научпоп веером на районные паблики (решение владельца 2026-06-14)

`⏱ 2026-06-14 · snooze 0 · fresh`

Запрос владельца (по итогам precision-спот-чека LLM-курации): новости науки и
познавательное должны публиковаться в районных пабликах для расширения кругозора —
«разносол» между местными новостями. **Источники нашлись в прод-БД** (уцелели от
Постопуса): `category='krugozor'` — SciTopus (-112289703), НауЧпок (-73083424),
Batrachospermum (-85330), Время-Вперёд (-65614662).

**Решения владельца 2026-06-14:** копия в нативный пост + атрибуция (не репост — у
репоста меньше охват и статы уходят источнику); все 16 районных+областных пабликов;
старт 1×/день вечером (20:00 MSK — пик «умного» чтения), при хорошем охвате +обед 13:00;
Время-Вперёд в общий поток `krugozor` (новой темы не плодим), ротация источников для
разносола.

- ✅ **Построено + ЗАДЕПЛОЕНО + ВКЛЮЧЕНО 2026-06-14** (PR [#230](https://github.com/Valstan/setka/pull/230)/[#231](https://github.com/Valstan/setka/pull/231)/[#232](https://github.com/Valstan/setka/pull/232)/[#233](https://github.com/Valstan/setka/pull/233)): `modules/krugozor_broadcast.py` —
  **сводка-режим** (финальная форма): за прогон ротацией собирает 2-4 свежих поста из
  РАЗНЫХ источников в один пост («сколько влезёт» по бюджету), лид-фото грид, анти-промо
  фильтр (`marked_as_ads`+легальные маркеры, без commercial-scoring). Атрибуция — **ссылкой
  текстом** (VK `copyright` для vk.com отбрасывает — gotcha, см. mailbox). Beat
  `krugozor-broadcast-evening` 20:00 MSK. **Включён на проде** (`KRUGOZOR_BROADCAST_DISABLED=0`),
  первая сводка верифицирован (4 источника). +сводка/промо тесты (1342 зелёных).
- ✅ **12 источников** (category=krugozor): были 4 (SciTopus/НауЧпок/Batrachospermum/Время-Вперёд)
  + добавлено 8 (ПостНаука/N+1/Образовач/Наука-и-жизнь/Антропогенез/TechInsider/Arzamas/Кот-Шрёдингера).
- ✅ **Стат-замер охвата выполнен 2026-06-24** (probe `scripts/probe_krugozor_reach.py`, read-only
  `wall.get`+baseline-сравнение, 16/16 регионов, 66 сводок за 6 дней). **Итог: оставить 1×
  пока.** Сводка читают (overall median **180** просмотров/пост, в крупных районах 300+), но в
  **типичном регионе это лишь ~47%** просмотров обычного локального поста (per-region медиана; pooled
  63% завышает — парадокс Симпсона, крошечные стены шумят), а вовлечённость **≈0** (медиана лайков 0,
  макс 2; репостов 0). Порог «уверенно читают» (≥50% от локального → 2× оправдан) не достигнут.
  **Решение 1×/2× — за владельцем:** охват средний → 2-й слот (обед 13:00) скорее каннибализирует, чем
  расширит; разумнее включить 2× как *измеряемый эксперимент* и перезамерить тем же probe через ~2 нед
  (сравнить суммарный дневной охват, а не на пост). Видео-источники → грид часто пуст (текст+ссылка) — норма.

### 📡 Радар — чтение Телеги + intake-бот «приём каналов» (запрос владельца 2026-06-14)

`⏱ 2026-06-14 · snooze 0 · fresh`

- ✅ **Радар↔Телега починен:** корень — `radar_subscriptions` пуст → поллер видел `sources:0`.
  Восстановил подписку valstan→gonba_life (прод-правка вне git, INSERT). TG-чтение через relay
  исправно. Поллер сразу взял пост + web-push.
- ✅ **Intake-бот построен + ВКЛЮЧЕН** (PR [#235](https://github.com/Valstan/setka/pull/235)–[#238](https://github.com/Valstan/setka/pull/238)): форвард поста канала боту → канал в
  радар. `modules/radar/bot_intake.py` (getUpdates-polling, **молчит чужим** + гейт на allowlist —
  боты публичные с трафиком), сервис `modules/radar/subscriptions.py` (DRY с API). Beat
  `radar-intake-bot` раз в минуту, offset в redis. **На AFONYA** (@malm_info_bot): env
  `RADAR_BOT_NAME=AFONYA` + `RADAR_BOT_ALLOWED_USERS=352096813` (прод-правка вне git).
- 🟢 **Браузер/TG-проверка владельцем:** форварднуть @malm_info_bot пост канала → канал в радаре.
- ✅ **TG-relay устойчив к тяжёлым/сопротивляющимся каналам** (отчёт владельца 2026-06-14: `@ASupersharij`
  не добавлялся, `@pezduza` — да). Корень-цепочка: (1) `resolve_source` подставлял `str(e)` — у httpx
  ReadTimeout текст пустой → бесполезная ошибка ([#255](https://github.com/Valstan/setka/pull/255));
  (2) relay-маршрут `/s/` **стримил** тело (не буферизовал, в отличие от `/media`) → стрим-столл httpx
  ([#256](https://github.com/Valstan/setka/pull/256)); (3) t.me отдаёт AJAX-вариант превью гигантов
  заглушкой без ленты и держит сокет → **фолбэк AJAX(6с)→GET(25с) с AbortSignal**
  ([#257](https://github.com/Valstan/setka/pull/257)/[#258](https://github.com/Valstan/setka/pull/258)),
  VPS-таймаут 30→45с. **Проверено на проде:** `resolve_source(ASupersharij)` 2.2с, `fetch_new` 15 постов
  за 2.6с. Внятные ошибки (таймаут/HTTP/нет-превью/пустая-лента) + ретрай. Урок для GOTCHAS (брайн): curl
  по HTTP/1.1 врёт (ждёт EOF на незакрытом сокете) — мерить relay реальным httpx (HTTP/2).

### 📻 Личный кабинет радара — выводы + CRUD источников (директива brain 2026-06-14 `radar-personal-cabinet`)

`⏱ 2026-06-14 · snooze 0 · fresh`

Дельта поверх Ф0/Ф1 (точка входа `/radar` + лента/архив/источники/push **уже были** —
не переделывал, см. ответ brain `mailbox/to-brain/2026-06-14-radar-cabinet-and-delivery-probe.md`).

- ✅ **Probe внешней доставки выполнен (#020):** **api.telegram.org ДОСТУПЕН с этого бокса**
  (myjino: 302/0.2с, intake-бот getUpdates тикает, алёрты уходят) — **G63 здесь НЕ материализуется**,
  TG-вывод текстом идёт напрямую через Bot API, relay для Bot API не нужен (relay только для
  чтения t.me/s/ и CDN-медиа). VK `wall.post` — уже установлен probe-ами рассылки/обложек (16/16).
  → оба внешних вывода buildable, построил сразу.
- ✅ **Построено (PR [#249](https://github.com/Valstan/setka/pull/249)):** миграция 045
  (`radar_subscriptions.is_active` пауза + таблица `radar_outputs`); модель `RadarOutput`;
  `modules/radar/delivery.py` (хук в поллере после web-push, под kill-switch
  `RADAR_DELIVERY_DISABLED`; курсор at-most-once по `item.id`, старт с MAX(id) — без бэклог-флуда;
  bounded 10/прогон; throttle 1с; per-output изоляция; fail_count/last_error для видимости #018);
  API `/api/radar/outputs` CRUD + `/test` + `PATCH /subscriptions/{id}` (пауза); UI вкладка «Выводы»
  (добавить TG/VK/лента, режим начало+ссылка/целиком, тест-кнопка, вкл/выкл) + пауза в «Источниках».
  Типы вывода: `feed` (дефолт, no-op — лента сама) / `telegram` (бот sendMessage) / `vk` (wall.post,
  текст+ссылка, медиа не рехостим — G64). +19 тестов (1400 зелёных).
- ✅ **Задеплоено 2026-06-14** (прод HEAD `a3b1e4c`): миграция 045 применена (ADD COLUMN + CREATE TABLE,
  аддитивно), restart web/worker/beat, health 200. **Браузер-смоук через Claude-for-Chrome** (сессия
  valstan): вкладка «Выводы» рендерит полную форму; round-trip `POST/GET/DELETE /api/radar/outputs`
  (feed) ✅; `PATCH /subscriptions/{id}` пауза ✅ (прод оставлен чистым — тест-артефакты удалены). Env
  не трогали (`RADAR_DELIVERY_DISABLED` дефолт-вкл, но выводы opt-in: пока юзер не создал внешний вывод —
  наружу ничего не уходит).
- ✅ **«Радиоточка» + подключение Telegram-лички в один клик** (запрос владельца 2026-06-14, PR #TBD):
  вкладка «Выводы» → **«Радиоточка»**; вместо технического дропдауна — кнопки «Подключить Telegram /
  ВКонтакте / Эл.почту / MAX». **Telegram готов:** `modules/radar/account_link.py` (одноразовый код в
  Redis, TTL 15м) + `POST /api/radar/link/telegram` (код + deep-link `t.me/<bot>?start=<код>` через
  getMe) + расширен `bot_intake` (`/start <код>` в обход allowlist — код авторизует → создаёт
  telegram-вывод в личку с chat_id) + модалка с deep-link/кодом и авто-поллингом подключения. VK-личка /
  почта / MAX — карточки «скоро» (нужна своя инфра: VK — захват user_id через сообщество; почта — SMTP
  (в проекте нет); MAX — probe API #020). +14 тестов (1412 зелёных).
- 🟢 **Браузер-верификация владельцем (реальная доставка):** «Радиоточка» → «Подключить Telegram» →
  открыть бота, Start → бот ответил «подключено» → дождаться нового поста в источниках → пришло в личку.
- ✅ **VK-личка — ПОСТРОЕНА** (запрос 2026-06-14, PR #TBD): probe (`scripts/probe_vk_messaging.py`) →
  владелец включил Long Poll + Сообщения на **Тестовом полигоне (137760500)** → re-probe `long_poll: ok` →
  построил бот-паттерном (как Telegram). `modules/radar/vk_intake.py` (Bots Long Poll: getLongPollServer →
  a_check → код из входящего сообщения → привязка; ts в Redis, failed-reinit); `account_link.link_vk` →
  вывод типа `vk_dm` (target=vk_id, config.group_id); delivery `vk_dm` → `messages.send` community-токеном;
  `POST /api/radar/link/vk` (код + ссылка на сообщество); beat `radar-vk-intake` раз в минуту; UI — кнопка
  «Подключить ВКонтакте» активна + модалка (код + ссылка + авто-поллинг). +13 тестов (1425 зелёных).
  **Деплой:** env `RADAR_VK_COMMUNITY_ID=137760500` + restart web/worker/beat (community-токен полигона в БД).
- 📌 **Архитектурное решение: НЕ использовать пользовательские VK-токены** для чтения/пересылки (вопрос
  владельца 2026-06-14). Причины: (1) VK 2026 привязывает user-токен к IP выпуска → с нашего VPS `error 5
  access_token was given to another ip address` (тот же блокер, что ловили с VALSTAN — см. ~~блокер~~ выше);
  (2) автополлинг/постинг с личного токена = «userbot», бан-риск для аккаунта пользователя; (3) нагрузка
  мала — мой парс-токен тянет всю сеть, доп. нагрузка радара ничтожна. **Для чтения — мой токен; для лички —
  бот-паттерн (community-токен `messages.send` + захват vk_id, когда юзер сам напишет сообществу).** Токен
  пользователя нужен только для репоста на ЕГО личную стену — не наш кейс (им нужна личка).
- 🟢 **Backlog (suggest-добавки директивы, по мере надобности):** instant vs сводка per-вывод;
  keyword-фильтр per-источник; индикатор «источник замолчал»; счётчик непрочитанного. Креды
  внешних выводов для **не-владельца** radar-юзера (свой бот/VK-токен) — пока pilot=владелец
  переиспользует общие; мультиюзер-креды = отдельная нитка.

### 🧩 Brain-директивы 2026-06-14 (recommend, probe-first)

`⏱ 2026-06-14 · snooze 0 · fresh`

- ✅ **Сетевая рассылка + внутренний планировщик — ПОСТРОЕНА и ЗАДЕПЛОЕНА 2026-06-14**
  (probe-ответ [#242](https://github.com/Valstan/setka/pull/242) → MVP [#243](https://github.com/Valstan/setka/pull/243) → QA-фиксы [#246](https://github.com/Valstan/setka/pull/246)).
  `modules/broadcast/` (dispatcher + service) + миграция 044 (`broadcast_campaigns`/`_targets`/`_publications`)
  + beat `broadcast-dispatch` (раз в минуту) + `broadcast-watchdog` (#018) + API/UI `/broadcast`.
  Канон владельца соблюдён (свой беат `wall.post` немедленно, НЕ VK-отложка); переиспользует
  `VKPublisher.create_with_policy` + ad-CRM-примитивы; idempotency per-(цель,прогон) ON CONFLICT claim
  + reclaim stale-pending; throttle ≥5с; повтор N раз; per-target изоляция. 🟢 _Остаток:_ браузер-проверка
  владельцем (собрать тест-кампанию на 1-2 паблика, запланировать, убедиться что пост вышел) +
  опц. вариация per-target (`vary_per_target` — forward-compat поле, дизайн вариации за brain/владельцем).
- ✅ **Починка загрузки картинок (413) + UX удаления + кликабельные ссылки 2026-06-18**
  (PR [#262](https://github.com/Valstan/setka/pull/262) + прод-правка nginx вне git). Загрузка >1 МБ
  падала с 413: у myjino HTTPS обрывается на edge-прокси → трафик идёт на **nginx:80 (Block 3)**, а
  не на 443; у Block 3 не был задан `client_max_body_size` (дефолт 1 МБ). **Фикс: `client_max_body_size
  20m` на уровне `http` в `/etc/nginx/nginx.conf`** (наследуют все блоки; бэкап `nginx.conf.bak-413`) —
  проверено по реальному пути (2/8 МБ → не 413). Шаблон: явная красная кнопка удаления + бейдж «в посте»
  + подсказка про `https://`-префикс (VK сам линкует). 🟢 _Остаток:_ подтверждение владельцем, что
  >1 МБ грузится; если ВСЁ ЕЩЁ 413 — лимит на edge-прокси myjino (вне nginx), копать отдельно.
- ✅ **Починка: рассылка слала ТОЛЬКО текст, картинки терялись 2026-06-19** (PR #TBD). Корень из
  лога worker: диспетчер грузил фото **community-токеном** → VK `[27] method is unavailable with
  group auth` (тот же барьер #27, что у `wall.edit`/`stats.get` — brain GOTCHAS) → `upload_wall_photo`
  возвращал None по каждой → пустая attachment-строка кэшировалась → все посты текстом (текст шёл, т.к.
  `wall.post` фолбэчит на user-токен). **Probe** `scripts/probe_wall_upload_token.py` (read-only,
  `getWallUploadServer`): **16/16 целей заливаются user-токеном, 0/16 community** (все [27]). **Фикс:**
  грузим **user-токеном** админа + **по каждой целевой группе отдельно** (owner фото = эта группа; одну
  строку на все цели нельзя — owner-mismatch, VK дропает); кэш `campaign.attachments` стал JSON-картой
  `{gid: 'photo..'}`. Тот же латентный баг (community-токен на стену) исправлен в `ad_cabinet`
  (`_build_wall_attachment`/`_upload_request_photos`, 0 публикаций → не проявлялся) + поправлен docstring
  `vk_wall_photo_upload`. +новые тесты (per-target attach, parse-map, text-only/no-rebuild). 🟢 _Остаток:_
  деплой (без миграции, restart worker/web) + браузер-проверка владельцем (картинка в опубликованном посте).
- ⏳ **Генератор обложек сообществ** (`...2026-06-14-community-cover-template-generator.md`): шаблон-сборщик
  cover'ов (фон от владельца → название+брендинг → upload), пилот Верхошижемье. **Probe cover-API выполнен
  2026-06-14** ([#244](https://github.com/Valstan/setka/pull/244), `scripts/probe_cover_api.py`, ответ brain
  `mailbox/to-brain/2026-06-14-community-cover-api-probe.md`): **16/16 пабликов `can_set` через user-токен
  VALSTAN** (владелец админ везде — G19-барьер НЕ материализовался), 11/16 с обложкой (референсы), 5 без
  (вкл. пилот Верхошижемье). **Мяч у brain↔владельца:** brain собирает промт фона по референсам → владелец
  генерит фон → SARAFAN строит сборщик (Pillow 1920×768 + название + брендинг → `saveOwnerCoverPhoto`).
  До прихода фона сборщик не строить (вход не определён).

### 📣 Программа автоматизации рекламного CRM (MANDATE-директива brain 2026-06-12)

`⏱ 2026-06-13 · snooze 0 · fresh · ПОСТОЯННАЯ программа (brain пингует «срез в неделю»)`

Сквозная автоматизация цикла `заявка → обработка → оплата → планирование → размещение →
удаление-по-сроку → просмотры → учёт` в **один кабинет**. Карта as-is + план — `mailbox/to-brain/2026-06-13-ad-crm-automation-asis-map-and-plan.md`. Директива — постоянная: brain
раз в неделю инициирует следующий срез. **Эту таблицу держать актуальной** (статусы звеньев),
чтобы еженедельный выбор был быстрым.

**Карта звеньев (auto / semi / manual / ОТСУТСТВУЕТ):**

| # | Звено | Статус | Примечание |
|---|---|---|---|
| 1 | Детект (предложка + ЛС) | AUTO | 2 beat-скана + classifier |
| 2 | Обработка заявки | SEMI | ответ в 1 клик, тред, маршрутизация |
| 3 | Оплата | ⏳ SEMI + авто-алёрт (С4 построен) | ручной ввод + Telegram-напоминание о должниках (>3 дн.) + фильтр/плашка; полный авто-приём денег упирается в банк-API |
| 4 | Планирование | SEMI | composer мультидата → VK-отложка |
| 5 | Размещение | AUTO | VK публикует, reconcile-beat фиксирует |
| 6 | Удаление по сроку | ⏳ AUTO (С2 построен) | expires_at + beat `expire-ad-posts-daily` 03:30; деплой+верификация |
| 7 | Просмотры рекламы | ⏳ AUTO (С3 построен) | wall.getById → views/likes/reposts + beat `collect-ad-publication-stats-daily` 04:30 + кнопка; деплой+верификация |
| 8 | Учёт / статистика | SEMI | воронка + графики + таймлайн |

**Решения владельца 2026-06-13:** срез С1 первым; формат 4 вкладки; срок поста (С2) — задаётся
при планировании; оплата (С4) — ручной ввод + авто-алёрт должников.

**Срезы:**
- ✅ **С1 — единый кабинет.** Свод `/ad-cabinet` + `/ad-crm` в один раздел `/ad` с 4 вкладками
  (Входящие заявки · Клиенты и воронка · Планировщик и публикации · Статистика). Построено и
  задеплоено 2026-06-13 ([PR #217](https://github.com/Valstan/setka/pull/217), прод HEAD `6f9fd06`,
  health 200). Старые пути → редиректы на `/ad`, nav-пункт «Реклама». **Браузер-верификация
  владельцем пройдена 2026-06-13** (все вкладки работают).
- ⏳ **С2 — авто-удаление поста по сроку** (закрывает дыру #6). **Построено 2026-06-13** (ветка
  `feat/ad-post-expiry`): миграция 041 (`expires_at` на `ad_scheduled_posts`/`ad_publications`,
  `removed_at` на публикации); `expires_at` задаётся в композере (`expire_days` «N дней от публикации»
  ИЛИ `expire_at` явная дата — приоритет у даты; опционально → NULL = висит вечно); reconciler
  переносит срок на публикацию; новый `modules/ad_cabinet/post_expirer.py` + beat-таска
  `expire-ad-posts-daily` (03:30 MSK) снимает вышедшие посты с истёкшим сроком (`wall.delete` →
  status `removed` + `removed_at` + событие 'removed' actor=system). Снимаем **независимо от оплаты**
  (решение владельца; должники — С4). +13 тестов. **Задеплоено 2026-06-13** ([PR #218](https://github.com/Valstan/setka/pull/218),
  миграция 041 применена, 4/4 active). 🟢 _Остаток:_ браузер-верификация (поле срока в планировщике,
  «снять DATE» в списке, фактическое снятие по сроку).
- ⏳ **С3 — просмотры рекламных постов** (закрывает дыру #7). **Построено 2026-06-13** (ветка
  `feat/ad-publication-stats`): миграция 042 (`views`/`likes`/`reposts`/`stats_updated_at` на
  `ad_publications`); `modules/ad_cabinet/publication_stats.py` тянет метрики через `wall.getById`
  (переиспользует стат-стек `modules/vk_monitor`, батч до 100, user-token админа видит просмотры);
  beat-таска `collect-ad-publication-stats-daily` (04:30 MSK) + кнопка «Обновить просмотры» в карточке
  (`POST /clients/{id}/refresh-stats`); метрики в строках публикаций + `total_views` в воронке;
  **отчёт клиенту** (`GET /clients/{id}/stats-report` → текст вставляется в чат, оператор отправляет).
  Решение владельца: просмотры+лайки+репосты, авто раз в день + кнопка, оператору в CRM + отчёт
  клиенту. +9 тестов. **Задеплоено 2026-06-13** ([PR #219](https://github.com/Valstan/setka/pull/219),
  миграция 042 применена, 4/4 active). 🟢 _Остаток:_ браузер-верификация (метрики в публикациях,
  кнопка «Обновить», «Отчёт клиенту» в чат).
- ⏳ **С4 — оплата: трекинг должников** (полу-авто). **Построено 2026-06-13** (ветка
  `feat/ad-debtor-tracking`): `modules/ad_cabinet/debtors.py` (`collect_debtors` — свод awaiting-оплат
  старше порога `AD_DEBTOR_DAYS`=3 по клиентам; `run_debtor_alert`); beat-таска `alert-ad-debtors-daily`
  (10:00 MSK) шлёт Telegram-список должников; фильтр «только должники» в списке клиентов
  (`GET /clients?debtors_only=1`) + плашка «должников: N на сумму X» в воронке (`/funnel`). Без миграции
  (используется существующая `ad_payments`). Решение владельца: порог 3 дня, Telegram раз в день,
  фильтр+плашка; полный авто-приём денег не делаем (банк-API). +7 тестов. **Задеплоено 2026-06-13**
  ([PR #220](https://github.com/Valstan/setka/pull/220), без миграции, beat-таска загружена, 4/4 active).
  🟢 _Остаток:_ браузер-верификация (чекбокс/плашка должников).
- ⏳ **С5 — сквозная «одна кнопка»** (финальная цель директивы «нажимается одной кнопкой»). **Построено
  2026-06-13** (ветка `feat/ad-one-click-accept`): `POST /requests/{id}/accept` композирует С1–С4 в один
  ход — `upsert_from_request` (клиент) → опц. `send_reply` (ответ) → `create_scheduled` (отложка с
  ценой/сроком, убрать оригинал, пометить заявку published, событие в таймлайн). UI: кнопка «Оформить» на
  карточке предложки + модалка (дата, цена, срок, ответ, тумблеры). Без миграции. +4 теста (1270 зелёных).
  **Деплой:** restart web (без миграции). 🟢 _Остаток:_ деплой + браузер-верификация (кнопка «Оформить» →
  модалка → один сабмит делает всё).

**Программа ad-CRM: первый круг + «одна кнопка» (С1–С5) построены за сессию 2026-06-13.** Цепочка сведена
в один кабинет, две дыры (#6/#7) закрыты, оплата под авто-алёртом, заявка оформляется одной кнопкой.

**Еженедельный ритм улучшений (модель владельца 2026-06-13):** раз в неделю я **сам** анализирую систему
+ статистику и предлагаю новое улучшение (ускорить обработку рекламы / качественно улучшить отклик с
рекламодателем), реализую в тот же день. Последний раунд — **Раунд 3 (2026-06-24/25)**: триаж инбокса
(с корректировкой премисы) + кнопка «Удалить» (см. ниже). Следующий раунд — по brain-пингу «срез в
неделю»: посмотреть статистику (`/funnel`, время отклика, сколько авто-приветствий ушло) и предложить
следующее.

**Раунды улучшений:**
- ⏳ **Раунд 1 (2026-06-13) — авто-приветствие рекламодателю** (ускорить первый отклик). **Построено**
  (ветка `feat/ad-auto-greeting`): миграция 043 (`ad_requests.greeting_sent_at`);
  `modules/ad_cabinet/auto_greeting.py` (`run_auto_greeting`) + beat-таска `auto-greet-ad-requests`
  (X:10/40, 8–22ч) шлёт свежим новым заявкам приветствие один раз. **Гейт #008-стиль, off по умолчанию:**
  env `AD_AUTO_GREETING_COMMUNITIES` (allowlist community vk_id — per-community включатель) + текст
  `AD_AUTO_GREETING_TEXT` (плейсхолдеры `{author_name}`/`{community_name}`) ИЛИ активный шаблон категории
  `ad_greeting`. Идемпотентно, только где писать можно (`can_message`, не группа-автор), anti-backlog окно
  6ч. +7 тестов (1277 зелёных). **Деплой:** миграция 043 + restart web/worker/beat + env владельца.
  ✅ **ВКЛЮЧЕНО НА ВСЕ ГРУППЫ 2026-06-14** (решение владельца): код [#241](https://github.com/Valstan/setka/pull/241)
  добавил wildcard `AD_AUTO_GREETING_COMMUNITIES=*`/`all` = все сообщества (вкл. будущие, без перечисления
  id). На проде выставлены env `AD_AUTO_GREETING_COMMUNITIES=*` + `AD_AUTO_GREETING_TEXT` (Вариант А, в
  кавычках), worker/beat перезапущены, beat `10,40 8-22` живёт. 🟢 _Остаток:_ нулевой (работает).
- ✅ **Раунд 2 (2026-06-14) — кнопка «Опубликовать»** (эмпирически из живой работы предложки):
  для бытовых бесплатных объявлений — моментальная публикация без платного пайплайна. POST
  `/ad-cabinet/requests/{id}/publish` (wall.post контента + фото → снятие оригинала → published →
  карточка уходит), кнопка в карточке (btn-info, confirm). Идемпотентно — **усилено 2026-06-14
  ([#247](https://github.com/Valstan/setka/pull/247)): `SELECT FOR UPDATE`** против двойного клика
  (конкурентный запрос мог дать дубль на живой стене). Задеплоено. 🟢 _Остаток:_ браузер-проверка.
- ✅ **Раунд 3 (2026-06-24/25) — триаж инбокса (brain GO) + кнопка «Удалить».** Две части:
  - **Триаж по score** ([#277](https://github.com/Valstan/setka/pull/277), `ec2bb56`): `/requests`
    сортирует `score desc, detected_at desc`, бейдж приоритета в карточке (≥3 🔥 / 1–2 / 0). **NB —
    probe скорректировал премису:** срез 06-22 «440 нетронутых тонет» оказался артефактом замера
    (`count(status='new')` по ВСЕЙ таблице `ad_requests`, а `/ad` фильтрует `route='ad_cabinet'` → там
    лишь ~13, все score≥1; 320 «score=0» — не-рекламные ЛС в `route='notifications'`, 311 уже `done`).
    Деструктивный bulk-skip **отменён** (нечего и нельзя чистить). Ответ brain с корректировкой +
    граблей замера — `mailbox/to-brain/2026-06-24-adcrm-inbox-triage-correction.md`.
  - **Кнопка «Удалить»** ([#278](https://github.com/Valstan/setka/pull/278), `fa6f28c`): POST
    `/requests/{id}/delete-post` — `wall.delete` (токен-роутинг с fallback) + `status='deleted'`,
    только для предложки (у `inbound_dm` нет `vk_post_id`). Отличие от «Пропустить» (та оставляет пост
    в VK). Атомарность: статус коммитим первым, лог best-effort; fail-closed при неудаче VK (502 +
    подсказка «Пропустить»). Адверсариал-ревью (16 агентов), 6 minor — ключевые применены.
    Оба задеплоены (restart web, без миграции). 🟢 _Остаток:_ браузер-проверка владельцем (бейджи/сорт
    в `/ad`; «Удалить» → пост исчезает из предложки VK + карточка уходит, видна по фильтру «Удалено»).
- ✅ **Раунд 4 (2026-06-25) — достижимость ЛС** ([#280](https://github.com/Valstan/setka/pull/280),
  `ef52245`, задеплоен). **Срез вскрыл корень узкого места отклика** (probe-before-build на проде, фильтр
  UI `route='ad_cabinet'` — G91): у **11 из 14 (79%)** входящих заявок `can_message=false` — VK не даёт
  сообществу писать в ЛС; авто-приветствие бессильно и это не показывалось оператору. Построено: бейдж
  «✉ ЛС закрыта» + кнопка «Ответить из личного VK» (deeplink `vk.com/im?sel=`) + метрика воронки
  «достижимо/закрыто». Без миграции. 🟢 _Остаток:_ браузер-проверка.

### 🧵 Непрерывная нить клиента — баланс «оплачено/израсходовано» (запрос владельца 2026-06-25)

`⏱ 2026-06-25 · snooze 0 · fresh`

Запрос владельца: вести клиента непрерывной нитью приём → общение → оплата → публикация → подсчёт →
контроль → **напоминание когда количество/способы публикаций превышают оплаченные счета** → проплата
следующего периода, **не покидая кабинет, не теряя клиента, не переключаясь между окнами**. Дизайн —
workflow understand→design→synthesize (5 инкрементов). **Решения владельца:** учёт **в штуках**
(публикациях), расход = только вышедшие платные (`published`), напоминание **при перерасходе**.

- ✅ **И1 — рублёвый баланс + единый helper + UI** ([#281](https://github.com/Valstan/setka/pull/281),
  `0876d79`, задеплоен): `modules/ad_cabinet/balance.py` (`compute_balance`/`summarize` — производная из
  существующих полей, не плодим третий источник правды; урок `AdOrderItem`); блок «Баланс нити» в карточке
  + крошка «нужна доплата» в списке + кнопка «Записать доплату». **Попутно починен баг:** `total_paid`
  считался по-разному в списке (`status=='paid'`) и карточке (`!='awaiting'`) — унифицировано. Без миграции.
- ✅ **И2 — штучный учёт пакета + Telegram-напоминание о перерасходе**
  ([#282](https://github.com/Valstan/setka/pull/282), `d231501`, задеплоен, **миграция 048 применена**):
  `ad_payments.units_paid` + `ad_clients.spend_alerted_at`; баланс «Куплено N − Вышло M = Осталось K»
  (`balance.units`); `modules/ad_cabinet/spend_balance.py` + beat `alert-ad-overspent-daily` 11:00 MSK
  (перерасход → Telegram, дедуп `spend_alerted_at`, сброс при доплате, кулдаун `AD_SPEND_ALERT_COOLDOWN_DAYS=3`);
  поле «за N публ.» в форме оплаты; крошка/строка пакета в UI. Фича активируется по мере ввода пакетов
  (нет ложных алёртов). restart web/worker/beat.
- 🟢 **Браузер-проверка И1+И2:** клиент → оплата с «за N публ.» → добавить публикаций > N → строка пакета
  краснеет «перерасход +N» + кнопка «Записать доплату»; через сутки — Telegram-список перерасходовавших.
- 🟢 **И5 (следующий шаг нити) — «всё в карточке»:** планировать публикацию + видеть заявки клиента прямо
  из карточки (overlay-модалка), без ухода на вкладку «Планировщик». Замыкает «не переключаясь между окнами».
- 🟢 **И3/И4 (опц., по запросу):** И3 — расширенный штучный учёт (период действия `coverage_end`); И4 —
  append-only книга движений `ad_ledger_entries` (аудит баланса). Делать только если простого баланса мало.

### 📡 Контент-радар — Ф0 (MANDATE-директива brain 2026-06-11)

`⏱ 2026-06-12 · snooze 0 · fresh`

Новый модуль внутри setka: личный агрегатор «источники (VK/TG/RSS) → лента radar-user'а → save-архив → web-push». Концепт — `brain_matrica/docs/plans/content-radar-concept.md`; решения владельца зафиксированы в директиве, не переоткрывать.

- ✅ **Probe #020 выполнен 2026-06-12** ([PR #196](https://github.com/Valstan/setka/pull/196), отчёт — `mailbox/to-brain/2026-06-12-content-radar-f0-probe-report.md`): механика `t.me/s/` работает, но **с VPS заблокирован весь Telegram кроме `api.telegram.org`** (включая медиа-CDN) → решение владельца: **egress-relay** (CF Worker). Web-push зелёный; HTTPS-техдомен уже есть (`3931b3fe50ab.vps.myjino.ru`, wildcard LE jino, G20).
- ✅ **Security-находка закрыта временно 2026-06-12:** операторский UI (вкл. `/tokens`) был доступен из интернета **без auth** → nginx basic-auth на все 3 server-блока (443 + оба 80), acme-challenge открыт, certbot жив. Креды: `ssh setka "sudo cat /etc/setka/web_basic_auth.txt"`. Бэкап конфигов: `/root/nginx-backup-20260612/`.
- 📋 **План Ф0 отправлен brain'у** — `mailbox/to-brain/2026-06-12-content-radar-f0-plan.md`. Срезы: Ф0.1 auth+изоляция (operator|radar роли, весь существующий UI под `require_operator`) → Ф0.2 sources+fan-out поллер (VK+RSS) → Ф0.3 TG-адаптер через relay → Ф0.4 PWA-лента+save-архив → Ф0.5 web-push.
- ✅ **Ф0.1 построен и ЗАДЕПЛОЕН 2026-06-12** ([PR #198](https://github.com/Valstan/setka/pull/198), прод HEAD `649adfa`): миграция 037 `radar_users` применена; auth-ядро `modules/radar/auth.py` (stdlib scrypt + stateless signed-cookie, смена пароля инвалидирует сессии через pwd-fragment); `middleware/auth_gate.py` — secure-by-default гейт (всё закрыто кроме `/login`, `/static`, `/api/health`, acme; `/metrics` только localhost; роль radar → только `/radar*`); страницы `/login` + `/radar`-заглушка; регистрация radar-юзеров по `RADAR_INVITE_CODE` (операторы — только CLI `scripts/create_radar_user.py`). +33 теста (1148 зелёных). **Прод-проверки:** login→200, защищённое API без cookie→401, браузер→302 /login, health открыт, 4/4 сервиса active. Оператор `valstan` создан, пароль: `ssh setka "sudo cat /etc/setka/web_operator_credentials.txt"`. `SETKA_WEB_SECRET` добавлен в `/etc/setka/setka.env`.
- ✅ ~~Хвост: снять nginx basic-auth~~ — снято 2026-06-12 сразу после проверки app-логина (бэкап конфигов остаётся в `/root/nginx-backup-20260612/`, файл `/etc/setka/web_basic_auth.txt` можно удалить при желании).
- ✅ **Ф0.2 построен 2026-06-12** (эта ветка): миграция 038 (`radar_sources`/`radar_subscriptions`/`radar_items`, uniq `source_id+external_id` — общий seen-стор); адаптеры `modules/radar/sources/` (vk — обёртка над wall.get-стеком, rss — httpx+feedparser, **новая зависимость feedparser==6.0.12**); fan-out поллер `modules/radar/poller.py` (источник поллится 1 раз на всех подписчиков, ON CONFLICT DO NOTHING, fail-isolation per-source) + heartbeat `setka:radar_last_polled` + watchdog #018 (алёрт только при живых подписках — retired≠dead R6); beat: poll `*/10` круглосуточно, watchdog hourly :12; API `/api/radar/` (subscriptions CRUD c резолвом VK-ссылок/screen_name и валидацией RSS-фида, лента с курсором по id). +37 тестов (1186 зелёных). **Деплой:** `pip install -r requirements.txt` (feedparser) + миграция 038 + restart web/worker/beat.
- ✅ **Ф0.2 задеплоен 2026-06-12** (прод HEAD `7a4684f`): feedparser установлен, миграция 038 применена, 3/3 active; live-smoke — подписка на «Гоньба - жемчужина Вятки», прогон 16:50 забрал 20 постов, `/api/radar/feed` отдаёт контент, без cookie 401.
- ✅ **Ф0.4 построен 2026-06-12** (эта ветка; порядок изменён — Ф0.4 раньше Ф0.3, тот ждёт CF-аккаунта): миграция 039 (`radar_saved` — СНИМОК контента, не FK на содержимое: items ретенцируются, сохранёнки вечны; + `radar_users.last_seen_item_id` — курсор новизны); `modules/radar/archive.py` (фото качаются в `RADAR_ARCHIVE_DIR`, дефолт `/var/lib/setka/radar_archive/<user>/<saved>/`; видео ссылкой; квота предупредительная: текст всегда, фото пока влезает; traversal-защита отдачи); API: `/api/radar/saved` CRUD + `media/{file}` (только владельцу) + `/feed/seen` (курсор только вперёд); UI `/radar`: вкладки Лента (бейдж «новое», догрузка курсором) / Архив (строка квоты) / Источники (добавить vk|rss, отписаться); PWA: manifest (имя **«Радар»** — решение владельца) + SVG-иконка + network-first SW (`/radar/sw.js` с `Service-Worker-Allowed`). +17 тестов (1203 зелёных). **Деплой:** миграция 039 + `mkdir -p /var/lib/setka/radar_archive` (owner valstan) + restart web.
- ✅ **Ф0.4 задеплоен 2026-06-12** (прод HEAD `ecb9b84`): миграция 039 применена, `/var/lib/setka/radar_archive` создан; live-smoke — пост Гоньбы сохранён в архив, фото 646 КБ легло на диск и отдалось 200.
- ✅ **Ф0.3 построен 2026-06-12** (эта ветка): CF-аккаунт владельца заведён (subdomain `zubazeirot.workers.dev`, токен в `/etc/setka/setka.env` #008); **relay уже задеплоен** — `infra/tg_relay/worker.js` (маршруты `/s/<ch>`, `/media?u=` с allowlist телеграмных CDN, `/health`; секрет `X-Relay-Secret`) через `scripts/deploy_tg_relay.sh` (голый CF API с VPS, без wrangler/node) на `https://tg-relay.zubazeirot.workers.dev`; env `TG_PREVIEW_RELAY_URL`+`TG_RELAY_SECRET` прописаны. **Находка деплоя:** t.me отдаёт datacenter-IP (CF) деградированную страницу — обычный GET даёт 1 сообщение, **AJAX-вариант (POST + X-Requested-With) даёт 3-5/страницу** — relay ходит им; для поллинга раз в 10 мин с БД-дедупом достаточно. TG-адаптер `modules/radar/sources/tg.py` (парсер по probe-селекторам, redirect=мёртвый канал, резолв @канал/t.me-ссылок); архив качает телеграмный CDN через relay (`_download_plan`); тип `tg` включён в API/поллер/watchdog/UI. +25 тестов (1228 зелёных). **Деплой:** только restart web/worker — env на месте, миграций нет.
- ✅ **Ф0.3 задеплоен 2026-06-12** ([PR #203](https://github.com/Valstan/setka/pull/203) + fix [PR #204](https://github.com/Valstan/setka/pull/204)): live-smoke — резолв `@gonba_life` через relay (title подтянулся), мёртвый канал → 400, поллер забрал 7 TG-сообщений в ленту. Два пост-факта: (а) CF Worker со стримингом тела вешает httpx по HTTP/1.1 → тело /media буферизуется (arrayBuffer); (б) **Telegram-CDN душит CF-egress на медиа до ~0.2-1 КБ/с** (файл 31 КБ: локально 2с, через relay не успевает за 120с) → **TG-фото в архив де-факто ссылкой** (graceful degradation: текст всегда, лента показывает CDN-URL напрямую в браузере), попытка скачивания короткая (20с). 🟡 _Ф1-варианты:_ фоновое скачивание с ретраями / другой egress.
- ✅ **Ф0.5 построен 2026-06-12** (эта ветка, **последний срез Ф0**): миграция 040 `radar_push_subscriptions` (несколько подписок на юзера: телефон+десктоп); `modules/radar/push.py` — VAPID из env (`RADAR_VAPID_PRIVATE_KEY` base64url raw + `RADAR_VAPID_SUBJECT`, ключи сгенерированы на проде #008), публичный ключ выводится на лету, рассылка из поллера после коммита (fan-out: {source→new} → {user→sum} → один push на юзера за прогон; `pywebpush` в thread; 404/410 → авто-удаление подписки; never-raises); API `/api/radar/push/` (vapid-public-key, subscribe идемпотентно по endpoint с ребиндом юзера/ключей, unsubscribe только своё); SW: push-обработчик (`tag` — новые пуши заменяют старый) + notificationclick (фокус/открытие /radar), cache v2; UI: колокольчик в nav (вкл/выкл, скрыт если push не настроен). Новые зависимости `pywebpush==2.0.3`+`py-vapid==1.9.4` (на проде установлены). +10 тестов (1238 зелёных). **Деплой:** миграция 040 + restart web/worker.
- 🟢 _Хвост Ф0.5:_ браузер-верификация владельцем — на `/radar` нажать колокольчик → разрешить уведомления → дождаться нового поста в источниках.
- 🟢 _Хвост Ф0.4:_ ~~PNG-иконки 192/512~~ — закрыто 2026-06-12: `icon-192/512.png` сгенерированы из геометрии SVG (`scripts/generate_radar_icons.py`, Pillow dev-only, PNG коммитятся готовыми), подключены в manifest/apple-touch-icon/push-нотификации; ~~ретенция `radar_items`~~ — закрыто 2026-06-12 (этот PR): beat-таска `radar-items-retention-daily` 03:20, порог `RADAR_ITEMS_RETENTION_DAYS` (дефолт 30), сохранёнки не страдают (снимок + FK SET NULL).
- ✅ **Отчёт мозгу о завершении Ф0** — `mailbox/to-brain/2026-06-12-content-radar-f0-complete.md` (итоги 5 срезов + 3 переносимые находки #009: t.me AJAX-обход для datacenter-IP, CDN-тарпит CF-egress, деплой CF Worker голым API).
- 🟢 _Браузер-верификация владельцем:_ зайти на `https://3931b3fe50ab.vps.myjino.ru/` → логин `valstan` (пароль по ssh выше) → дашборд работает как раньше; кнопка выхода в правом углу nav.

### 📡 Контент-радар — Ф1 (приоритизация brain 2026-06-13)

`⏱ 2026-06-13 · snooze 0 · fresh`

Порядок Ф1 утверждён brain ([`mailbox`-письмо `2026-06-13-content-radar-f1-prioritization.md`](../../brain_matrica/mailboxes/setka/from-brain/2026-06-13-content-radar-f1-prioritization.md)):
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

### 🔍 Универсальный tiered-поиск (pool #035, директива brain 2026-06-09)

`⏱ 2026-06-10 · snooze 0 · watch (построено и задеплоено 2026-06-10 — PR #191, миграция 036, health 200; остаток — браузер-верификация владельцем)`

Запрос владельца: все поля поиска находят введённую комбинацию **в любом месте** строки (substring), при нуле результатов — деградация к похожему (subsequence → fuzzy); многотокен AND, нормализация номеров, RU↔EN раскладка, подсветка, единый shared-модуль. Канонический спек — [pool #035](../../brain_matrica/cross-project-ideas/ideas/035-universal-tiered-search.md). Решения владельца 2026-06-10: поле на `/communities` — да; подсветка — везде, где дёшево; нитка целиком за одну сессию.

**Инвентарь полей (#022, снят 2026-06-10):** свободный текст-поиск был всего в 3 местах — `/posts` (фильтр был **мёртв**: значение не отправлялось на бэкенд, как и фильтр статуса), `/subscriber-growth` (клиентский `.includes()`), `/ad-crm` (серверный `ILIKE '%q%'`). Typeahead городов матчит VK API — неприменимо; ~15 остальных фильтров — exact-enum. На `/communities` поиска не было вообще.

**✅ Построено 2026-06-10 (вся нитка Ф0–Ф3):**
- **Ф0** — shared-модуль `web/static/js/search_match.js` (подключён в `base.html`): нормализация (lower, ё→е, compact-номера) → substring → subsequence → fuzzy (биграммный Dice) → многотокен AND → ранжирование exact>prefix>word-prefix>substring>subsequence>fuzzy → подсветка `<mark>` → RU↔EN ретрай. Серверное зеркало нормализации — `utils/search_query.py` (+14 тестов).
- **Ф1** — `/subscriber-growth` (tiered + подсветка), `/posts`: оживлены мёртвые фильтры (status, search), серверный `?q=` в `web/api/posts.py` (многотокен AND ILIKE), debounce 400ms, клиентский RU↔EN ретрай, подсветка в выдаче.
- **Ф2** — `/communities`: новое поле поиска (name + VK ID), чисто клиентская фильтрация без рефетча (список и так грузится целиком). Подсветка в имени невозможна — там редактируемый `<input>`.
- **Ф3** — `/ad-crm` `?q=`: tiered на сервере — substring AND-токены (+compact-матч телефона в contact) → RU↔EN ретрай → `pg_trgm similarity` fuzzy (только на Postgres-диалекте). Миграция 036 (`CREATE EXTENSION pg_trgm` + 2 GIN). +5 тестов list_clients.

**Остаток:** только браузер-верификация владельцем — внесена в «Пакет браузер-верификаций» (🟢 Идеи). Деплой выполнен 2026-06-10: `git pull` + миграция 036 (заодно migrate.py дозаписал в журнал 034/035, применённые ранее вручную — идемпотентные) + restart web; `pg_trgm` стоит, health 200, smoke `?q=` на ad-crm и posts → 200.

### 🤖 LLM-курация сводок — Фаза 1 (shadow PoC, письмо brain 2026-06-07)

`⏱ 2026-06-07 · snooze 0 · ✅ ЗАКРЫТО 2026-06-30 — развилка решена владельцем: ветка (B), фильтр релевантности свёрнут как направление. Секция оставлена как record (worked-example shadow-gate).`

Оценка `suggest`-предложения brain: возложить фильтрацию релевантности сводок на LLM (ловит то, что алгоритм пропускает — рекламу, нерелевантное району, **перефразированные дубли**, которые simhash не берёт). **Скорректировали дизайн brain'а:** вместо enforcing (публикуем только approved → сцепляет публикацию с доступностью desktop'а, риск G26/протухание вечерних волн) — **Фаза 1 в shadow-режиме**: публикуем как сейчас, параллельно паркуем опубликованные посты и мерим, сколько LLM бы отсеяла (дельта над алгоритмом) + precision + токены. Нулевой риск, нулевой сдвиг тайминга, fail-open by design.

- ✅ **Построено (ветка `feat/bulletin-llm-curation-shadow`):** миграция 035 (`bulletin_curation_runs`), ORM `BulletinCurationRun`, изолированный `modules/curation/recorder.py` (своя сессия + never-raises, гейт `BULLETIN_CURATION_SHADOW_ENABLED` + allowlist `BULLETIN_CURATION_REGION_CODES`), 2 шва (cascaded_bulletin + parsing_scheduler пост-публикация), CLI `scripts/curate_pending.py` (`--list/--apply/--stats`), `/curate` + рубрика `docs/curation/rubric.md`. +9 тестов (1085 зелёных).
- ✅ **Деплой** — состоялся (миграция 035 применена, `BULLETIN_CURATION_SHADOW_ENABLED=1` + регион Малмыж в env, worker/beat перезапущены): подтверждено наличием накопленных прогонов в `bulletin_curation_runs`.
- ✅ **Прогон PoC (≈неделя, 1 регион)** — `--stats` снят **2026-06-14**: 48 reviewed (+94 pending), 101 пост, **flag-rate 18.81%** (19 drop), токены 25 450 (~530/прогон, ~252/пост). Состав drop: не-район ≈13, реклама 3, развлек-репост 2 — всё в рубрике.
- ✅ **ack brain'у с цифрами** — отправлен 2026-06-14 (`mailbox/to-brain/2026-06-14-llm-curation-poc-stats.md`). Honest-нюанс: precision скрипт не инструментирует (вердикты пишет сам /curate-луп), истинный ground-truth релевантности «району» — владелец.
- ✅ **Спот-чек владельца пройден 2026-06-29** (гейт precision снят): владелец прошёл 19 флагов
  (committed-артефакт `docs/curation/spotcheck-19.md`), подтвердил снять **только 3** (#7 авто-реклама,
  #14 федер. набор по контракту мульти-регион, #15 распущенно-военный репост). Остальные 16 — KEEP
  («разносол приветствуется»). **Precision его глазами = 3/19 ≈ 16%.** Премиса «не-район = мусор»
  владельцем опровергнута: федеральные/исторические/областные/развлек-посты он хочет ради разнообразия;
  реальная ценность LLM — фильтр спама/неуместного, не «релевантности району». Отчёт brain:
  `mailbox/to-brain/2026-06-29-curation-spotcheck-precision.md`.
- ✅ **Развилка Фазы 2 решена 2026-06-30 — владелец выбрал (B): свернуть LLM-фильтр релевантности**
  (письмо brain `from-brain/2026-06-30-spotcheck-decision-wind-down-mcp-r18.md`; ответ
  `mailbox/to-brain/2026-06-30-curation-wind-down-ack.md`). Премиса «не-район = мусор» опровергнута
  («разносол» владельца ближе к разрешающей логике алгоритма) → **enforcing не проектируем, рубрику не
  переоснащаем (ветка A отброшена)**. Гейт «measure-before-enforce» отработал как задуман — поймал
  несозревший фильтр (16% precision) до боли. **Shadow-таблица `bulletin_curation_runs` сохранена как
  аудит** (brain: «не сносить, дёшево и полезно для ретроспективы»); shadow-recorder оставлен пассивным
  аудит-логом на проде (env `BULLETIN_CURATION_SHADOW_ENABLED`, выключается одним движением — прод не
  трогали). **Если когда-нибудь вернёмся к LLM-курации** — целиться в узкий класс **спам/неуместное**
  (то, что владелец реально снял: мульти-регион вербовка, авто-реклама, распущенно-военное), НЕ в
  «релевантность району» (наводка brain). mcp-обёртка `gateway_mcp/` → REFERENCE R18 (бонус-ack).

### 🌍 Big idea — модуль авто-регистрации регионов и сообществ

**MVP + recheck закрыты 2026-05-22** (см. `DEV_HISTORY.md`):
- ✅ Миграция 011: `community_candidates` + `regions.vk_city_id/center_city` + `communities.health_*` + composite индекс (region_id, vk_id).
- ✅ Backend MVP: `modules/discovery/{vk_search,ai_categorizer}.py`, `tasks/discovery_tasks.py`.
- ✅ Web API: `/api/discovery/{cities,trigger,candidates,candidates/{id},candidates/bulk}`.
- ✅ UI: `/regions/new` wizard с auto-resolve VK city, `/regions/<code>/discovery` с фильтрами / approve modal / bulk-actions.
- ✅ **Recheck (итерация 2)**: `modules/discovery/health_check.py` + `tasks.discovery_tasks.recheck_communities_for_region` / `recheck_all_active_regions`. Beat entry `discovery-recheck-weekly` (Mon 04:00 MSK). Telegram-alert по итогам. VK errors 15/18/100/203 → `dead`; пост старше `dormant_days` (default 60, override `region.config['dormant_days']`) → `dormant`; AI drift при confidence ≥ 70 → `changed_category` + `suggested_category`.
- ✅ +90 тестов всего по big idea (60 MVP + 30 recheck), 360/360 зелёных.

### Итерация 3 — localities-driven discovery + human-in-the-loop AI

Начато 2026-05-25 после жалобы на качество подбора кандидатов: discovery для `tuzha` возвращал крупные общегородские паблики Кирова/Татарстана без географической привязки к Тужинскому району. Корень — VK `groups.search` не строгий + сортировка по `members_count` усиливала перекос. Параллельно — `GROQ_API_KEY` 403 на проде (нет бюджета, см. 🔴 блокеры) → AI-категоризация перестала фильтровать релевантность.

Решение в 3 PR:

- ✅ **PR 1 — backend** ([#39](https://github.com/Valstan/setka/pull/39)): миграция 012 (`community_candidates.ai_is_relevant`), `vk_search.py` принимает `localities`/`keywords` из `region.config`, hard relevance-filter, сортировка `(matched_localities desc, members_count desc)`. +34 теста.
- ✅ **PR 2 — UI «Подготовка района»** ([#40](https://github.com/Valstan/setka/pull/40)): `/regions/<code>/prepare`. Два блока: localities (OSM Overpass auto-suggest + clipboard-prompt fallback) и discovery_keywords. API: `GET/PATCH /api/discovery/regions/{code}/config`, `GET /api/discovery/osm-localities`. +24 теста.
- ✅ **PR 3 — AI-batch через clipboard** ([#41](https://github.com/Valstan/setka/pull/41)): `/regions/<code>/discovery/ai-batch`. Чанки по 30, готовый prompt + clipboard, robust JSON parser. API: `GET /ai-batch`, `POST /ai-batch/apply`, `GET /ai-batch/status`. Badge «✓/✗ районный» + фильтр «скрыть нерелевантных» на discovery. +11 тестов.
- ✅ **Релиз на прод 2026-05-25**: HEAD `7ba2560`, миграция 012 применена, restart всех 3 сервисов, health 200. Endpoints `/regions/tuzha/prepare`, `/regions/tuzha/discovery/ai-batch` → 200. AI batch status: `total: 147 pending, processed: 0`.

⏳ `⏱ 2026-05-25 · snooze 3+ · stale → ре-триаж 2026-06-10: переформулировано — влит в «Пакет браузер-верификаций владельцем» (🟢 Идеи ниже), отдельным пунктом не висит` **Осталось — практический smoke на tuzha** в браузере: `/regions/tuzha/prepare` → OSM auto-suggest или ChatGPT prompt → save → re-trigger discovery → должно отвалиться ~120/147 нерелевантных. Затем `/discovery/ai-batch` → прогнать через нейросеть → approve → commit. Это пользовательский шаг (нажимать кнопки), не код.

Зачем не ждать Groq: пользователь явно сказал — бюджета на API нет. Human-in-the-loop через clipboard бесплатно (юзер тратит свой ChatGPT/Claude.ai тариф), прозрачно, юзер видит точный prompt. Не масштабируется на еженедельный recheck — но и не нужно, recheck без AI работает (он смотрит health, не категоризацию), `changed_category` детекция временно отключена.

### Рефакторинг модуля уведомлений VK (этапы 1-5)

Все этапы закрыты:

2026-05-21:
- 0 — Fallback на user-token при VK error 15/27 + `keep_if_empty` в storage.
- 1 — Полный сбор комментариев: пагинация по offset, thread.items, `max_total_comments` 300→5000 safety cap.
- 2 — BaseVKChecker (DRY), удалён UnifiedNotificationsChecker и dead-code, окно 8-22 только в crontab.
- 3 — Storage history + виджет «активность за 24ч» (Chart.js) + API `/history` `/stats`.
- 4a-mini — `like_comment` от имени сообщества, mark-as-handled (7d), виджет «Горячие посты».
- 5 — Prometheus метрики + token-health watchdog с Telegram-alert и 6h cooldown.
- hot-fix-2 — VKClient.api_call propagates error_code + regex fallback в `_invoke`.

2026-05-22:
- 4b — Inline-reply на коммент (`wall.createComment` + `from_group`); AI-черновик через Groq (`llama-3.1-8b-instant`); шаблонные ответы на сообщения + CRUD-страница `/templates` + миграция 008; Telegram inline-кнопки с deep-link на `#section=...`. Доделано всё, что откладывалось.

Техдолги по audit'у token routing (продолжаются):

### 🆕 Техдолги по audit'у token routing

Закрыто 2026-05-21 (см. `DEV_HISTORY.md`):
- ✅ VK Captcha rate-limit — добавлен `GLOBAL_PUBLISH_INTERVAL_SECONDS=1.5` (class-var на VKPublisher).
- ✅ Косметика лога `via=community-token` после fallback — `_call_wall_post` теперь возвращает `(response, via_label)`, метка вычисляется по фактическому пути.
- ✅ wall.repost больше не пробует community-token (VK API физически не поддерживает) — добавлен `_USER_TOKEN_ONLY_METHODS={'wall.repost'}`.

Закрыто 2026-05-22 (см. `DEV_HISTORY.md`, этап 4b):
- ✅ Inline-reply на коммент из карточки `/notifications`.
- ✅ AI-черновик через Groq в модалке ответа (кнопка ✨).
- ✅ Шаблонные ответы на сообщения сообщества + CRUD-страница `/templates`.
- ✅ Telegram inline-кнопки с deep-link на `/notifications#section=...`.

Остаются:

- ~~**Удалить мёртвый код**~~ (закрыто 2026-05-22, см. DEV_HISTORY): `cross_region_repost.py` оказался уже удалён ранее; `correct_workflow` + `publish_bulletin_to_main_group` удалены целиком вместе с beat-entry `monitoring-hourly` и `tasks/correct_workflow_tasks.py`.
- ~~**Мигрировать старый `vk_publisher.py`**~~ (частично закрыто 2026-05-22): deprecated стек удалён (`modules/publisher/publisher.py`, `modules/scheduler/scheduler.py`, `tasks/publishing_tasks.py`, `tasks/test_info_tasks.py`, `modules/test_info_scheduler.py`, `web/api/workflow.py`, `scripts/test_full_workflow.py`). Остаётся живой `web/api/publisher.py` (UI `/publisher`) — он использует кастомные методы (`get_group_info`, `publish_aggregated_post`, `get_target_group_id`), которых нет в extended. Миграция требует либо расширения extended-API, либо переписывания endpoint'ов. Записано в 🟢 идеи.
- ~~**Глобальный rate-limit на parse-token VITA**~~ — закрыто 2026-05-22 (`GLOBAL_PARSE_INTERVAL_SECONDS=0.4` в `VKClient`, per-process per-token). Cross-process variant (через Redis) на случай multi-worker Celery — записан в 🟢 идеи.

_Все запланированные этапы (0, 1, 2, 3, 4a-mini, 4b, 5) закрыты. См. `DEV_HISTORY.md`._

---

## 🟡 Техдолги

### Dead-code гигиена (#036, директива brain 2026-06-10)

- 🟡 `⏱ 2026-06-14 · snooze 0 · fresh` **Ежемесячный прогон `/deadcode`** — следующий ~2026-07-14. Сканер `scripts/deadcode_scan.py` (vulture + авто-allowlist Celery/pydantic/SQLAlchemy), триаженное подавлено в `scripts/deadcode_known.txt` → отчёт показывает только новую дельту. Report-only, удаление — обычным PR. **Прогон 2026-06-14:** 11 новых кандидатов → удалены 2 dead-хвоста (`utils/post_utils.py::format_number`, весь orphan-модуль `utils/image_utils.py::image_to_histogram_md5`), 9 false-positive/sleeping подавлены в known.txt (`verify_session_token` alive, `api_requests_in_progress`/`system_info` alive, 5 Prometheus-метрик без продьюсера — `sleeping`). Новый код рассылки (modules/broadcast) — без мёртвого кода.
- ~~**Разбор первого триажа: ~120 dead-кандидатов**~~ — закрыто 2026-06-12 четырьмя пакетными PR (делегировано владельцем «на твой выбор»): [#211](https://github.com/Valstan/setka/pull/211) carousel-цепочка (`vk_carousel_tasks.py` + orphan `carousel_manager.py`), [#212](https://github.com/Valstan/setka/pull/212) старые publisher'ы (`wordpress_publisher`/`telegram_publisher`/`event_distribution` + orphan `base_publisher`), [#213](https://github.com/Valstan/setka/pull/213) postopus-слой `modules/core` (остался только живой `calculate_post_score`), [#214](https://github.com/Valstan/setka/pull/214) россыпь utils/ + 6 декораторов metrics (−556 строк). Все цепочки orphan'ов прослежены (#028), подавления вычищены из `deadcode_known.txt`, 1236 тестов зелёные. ~~Мини-хвост `utils/post_utils.py::format_number`~~ — снят прогоном 2026-06-14 (см. выше).
- 🟢 `⏱ 2026-06-14 · snooze 0 · fresh` **5 Prometheus-метрик без продьюсера** (`vk_api_request_duration_seconds`, `db_queries_total`, `db_query_duration_seconds`, `posts_processed_total`, `posts_published_total`) — определены, но никем не инкрементятся (экспортятся пустыми). Помечены `sleeping` в known.txt (не удалял молча — удаление меняет surface `/metrics`). Кандидат на чистку при желании владельца (или дождаться, пока инструментируем).

### Рекламный кабинет (MVP 2026-06-02)

MVP: детект рекламы в предложке (`modules/ad_cabinet/classifier.py`, обёртка над `AdvertisementFilter` + предложка-сигналы) → инбокс `/ad-cabinet` (таблица `ad_requests`, миграция 021) → персонализированный ответ в 1 клик (полу-авто; VK error 901 → фолбэк на личный аккаунт). Деплой — отдельно через `/reliz` (миграция 021 + restart web/worker/**beat**). Открытые хвосты:

#### Единый роутер входящих ЛС (Этап 1, 2026-06-06) — директива brain

- ✅ **Этап 1 (R1-R3) — задеплоен 2026-06-06** ([PR #164](https://github.com/Valstan/setka/pull/164), прод HEAD `e12fa3a`, миграция 032 применена, 3/3 active). Багфикс потери не-рекламных ЛС: раздел «Уведомления» показывал живой VK unread-счётчик и ничего не хранил → не-рекламное ЛС, прочитанное при ad-скане, исчезало. Фикс: `dm_scanner` persist'ит **каждое** входящее ЛС в `ad_requests` до классификации (миграция 032: `route`, `handling_status`, `handled_at`); раздел «Уведомления» показывает не-рекламные ЛС из БД (блок «Входящие ЛС сообществ»); кнопки «Не реклама → в уведомления» / «Это реклама → в кабинет». UPSERT переоткрывает диалог при новом входящем. 🟢 _Остаток:_ браузер-верификация владельцем.
- ✅ **Этап 2 (R4-R5) — in-app переписка + нитка, задеплоен 2026-06-06** ([PR #165](https://github.com/Valstan/setka/pull/165), прод HEAD `6abcd62`, restart web, 3/3 active). VK-capability-probe выполнен ([scripts/probe_community_dm_capabilities.py](../scripts/probe_community_dm_capabilities.py), результат brain'у: `mailbox/to-brain/2026-06-06-community-dm-probe-result.md`): community-токен **читает историю** (`getHistory`) и **отвечает** (`messages.send`) — capability зелёная (ad-кабинет уже шлёт ответы в проде), `markAsUnread`-эквивалента нет. R4: кнопка «Ответить» в карточке ЛС уведомлений (модалка → `/api/notifications/messages/reply` community-токеном; после отправки `handling_status=done`). R5: «Переписка» тред-вью (`/api/ad-cabinet/requests/{id}/thread`); нитка = строка `ad_requests` по `(community, peer)`, новое входящее переоткрывает её (UPSERT скана). Только фронт (`notifications.js/html`), бэкенд переиспользован. 🟢 _Остаток:_ браузер-верификация владельцем (ответ из приложения доходит; тред показывается; новое входящее переоткрывает).

#### Интерактивность + слежение (серия 2026-06-05, задеплоено)

- ✅ **Кабинет стал «дневниковым» + слежение за публикациями/оплатой (задеплоено 2026-06-05, прод HEAD `7f5ea30`).** Серия PR [#152](https://github.com/Valstan/setka/pull/152)–[#157](https://github.com/Valstan/setka/pull/157): **история взаимодействий** (audit-log `ad_interactions`, миграция 028) + таймлайн с датой-временем каждого действия в карточке клиента; **оплаты** с банком (фикс-список `AD_PAYMENT_BANKS`) и статусом `awaiting`/`paid` + должники (миграция 029) + `GET /banks` (куда чаще платят); **заказы клиента** `ad_order_items` (миграция 030, из предложки или вручную); **прямой двусторонний чат** с клиентом в карточке; **авто-фиксация публикаций** отложки (beat `reconcile-scheduled-publications` X:45 — VK опубликовал → `AdPublication` + awaiting-оплата + событие в таймлайн); **графики** роста предложений/оплат + частоты банков. Миграции 028-030 применены, 3/3 active, smoke-test OK (`posts_parsed=3`), новые эндпоинты `/api/ad-crm/{funnel,banks,stats/timeseries}`→200. 🟢 _Остаток:_ браузер-верификация владельцем (таймлайн / оплаты-должники / заказы / чат / графики). **Фаза 4 (ML-классификатор)** и **фаза 5 (авто-правила/follow-up)** — остаются открытыми (см. ниже).

#### Кабинет 2.0 — roadmap (дизайн 2026-06-04)

Три блока расширения. **Блок B (планировщик отложки) — B1 реализован и задеплоен 2026-06-04.**

- ✅ **B1 — планировщик отложенных постов (задеплоен 2026-06-04).** Из кабинета формируется график постов по датам → нативная VK-отложка (`wall.post publish_date`), VK сам публикует. PR-цепочка: [#131](https://github.com/Valstan/setka/pull/131) seam (`publish_date`/`signed`/`set_post_comments`/`vk_wall_photo_upload`), [#132](https://github.com/Valstan/setka/pull/132) таблица `ad_scheduled_posts` (миграция 025) + API `POST/GET /scheduled` + `POST /scheduled/{id}/cancel` (`wall.delete`), [#133](https://github.com/Valstan/setka/pull/133) UI composer (мультидата + тумблеры from_group/signed/комменты + календарь). **Прод: HEAD `3f7e085`, миграция 025 применена, 3 сервиса active, health 200, `GET /scheduled`→200.** ⏳ _Остаток:_ браузер-верификация за владельцем (создать отложенный пост → проверить в VK-«Отложенных»). Известный нюанс: создание N постов в одну группу подряд тормозит на `POST_INTERVAL_SECONDS=5` (для 5 дат ≈20с) — приемлемо для MVP, при желании вынести в фон.
- ✅ **B2 — запланировать заявку из предложки (пересозданием через B1, задеплоено 2026-06-04, [PR #136](https://github.com/Valstan/setka/pull/136), HEAD `ab11cf9`).** Кнопка «Запланировать» на карточке заявки `/ad-cabinet` → переносит заявку в composer планировщика (сообщество + текст предложки префиллятся), оператор задаёт даты → `wall.post(publish_date=…)` создаёт **новый** отложенный пост (движок B1), оригинал убирается из предложки (`wall.delete`, тумблер «убрать оригинал», вкл. по умолчанию) и заявка → `published`. Бэкенд: `ScheduleCreateIn.remove_original` + `source_ad_request_id` в `create_scheduled` (удаление только при `scheduled_n>0` — не теряем заявку при провале). +4 теста. ⏳ _Остаток:_ браузер-верификация за владельцем.
  - ⚠️ **Исходный замысел «in-place `wall.edit`» оказался технически невозможен (VK-probe 2026-06-04).** `wall.edit` по предложенному посту: community-token → `[27] method is unavailable with group auth`; user-token (даже **админ** группы) → `[15] Access denied`. На обычном (не-suggested) посте `wall.edit`+`publish_date` работают — дело именно в suggested-статусе: **предложку правит только web-UI VK, не API.** Поэтому «родная подпись Предложил(а): …» при пересоздании НЕ сохраняется (становится пост сообщества). Диагностика — `scripts/probe_wall_edit_publish_date.py` (повторяемый probe, на случай если VK когда-нибудь откроет метод).
  - 🟢 _Хвост B2:_ медиа автора предложки (`photo{user}_{id}`) при пересоздании **не переприкрепляется** (доступ чужого фото) — оператор выбирает офферные картинки / грузит свои. Для рекламы обычно и так свои материалы. Если понадобится — скачивать+перезаливать оригинальные фото на стену (как `vk_wall_photo_upload`).
- ✅ **A — реклама во входящих ЛС + диалог из кабинета (готово 2026-06-04; бэкенд [PR #138](https://github.com/Valstan/setka/pull/138) задеплоен на `ca2f850`; UI — PR 2).** Реклама ловится не только в предложке, но и во входящих ЛС сообществ; оператор отвечает прямо в диалог и видит переписку.
  - **Бэкенд (#138, на проде):** миграция 026 (`ad_requests.origin` `suggested`/`inbound_dm`, `vk_post_id` nullable, `last_message_id`, частичный уникальный индекс дедупа `(community_vk_id, peer_id) WHERE origin='inbound_dm'`); `VKDialogsChecker` (`messages.getConversations`, нормализует последнее входящее сообщение в post-совместимый формат); `modules/ad_cabinet/dm_scanner.py` (`run_dm_scan`/`scan_region_dialogs`, тот же `classifier.classify`); celery `scan_inbound_dm_ads` + beat `scan-inbound-dm-ads` (X:05/35, 8-22); `origin`-фильтр в `GET /requests`. Для inbound-ЛС `can_message=True` сразу → существующий `/send` отвечает без доработок.
  - **UI (PR 2):** фильтр «Все/Предложка/Личка» на `/ad-cabinet`; бейдж источника на карточке; для ЛС — ссылка на диалог (`dialog_url`), кнопка «Показать переписку» (тред-вью через `VKDialogsChecker.fetch_history` + `GET /requests/{id}/thread`), «Запланировать» скрыта (вместо неё «Ответить в диалог»). +9 тестов. **Деплой PR 2:** только restart `setka` (web) — миграций нет.
- ✅ **C — учёт оплат/публикаций (бывшая фаза 3 CRM). Бэкенд + UI готовы 2026-06-04.** `ad_clients`/`ad_payments`/`ad_publications` (ключ `author_vk_id`), связь заявка/пост→клиент, воронка detected→contacted→scheduled→published→paid→lost. Задел в `ad_scheduled_posts.client_id`/`price` (миграция 025).
  - **Бэкенд (задеплоен на `e072b12`, PR #141):** миграция 027 (`ad_clients` с уник. `author_vk_id` + `stage`; `ad_payments` FK→client CASCADE; `ad_publications` FK→client SET NULL; `ad_requests.client_id` + FK; FK на `ad_scheduled_posts.client_id`, обещанный в 025). ORM-модели `AdClient`/`AdPayment`/`AdPublication` + `AdRequest.client_id`. API `web/api/ad_crm.py` (`/api/ad-crm`): clients CRUD + агрегаты (оплачено/публикаций скаляр-подзапросами), `upsert-from-request/{id}` (свод заявок предложки+ЛС по `author_vk_id`), payments add/delete (→`paid`), publications add/delete (→`published`, не понижает `paid`), `GET /funnel`. +21 тест. Прод: миграция 027 применена, 3/3 active, `GET /api/ad-crm/funnel`→200.
  - **UI (этот PR):** страница `/ad-crm` (`ad_crm.html` + `ad_crm.js`): воронка-плашки по стадиям + итоги (оплачено/публикаций), фильтр по стадии + поиск, карточки клиентов (inline-смена стадии, раскрытие деталей с правкой имени/контактов/заметок, таблицы оплат и публикаций с inline-добавлением/удалением), модалка «Завести клиента». Кнопка «В CRM» на карточке заявки `/ad-cabinet` (дёргает `upsert-from-request`). apiClient CRM-методы в `api.js`. Нав-пункт «CRM рекламы». +1 тест (роуты подключены). **Деплой:** только restart `setka` (web) — миграций нет. _Браузер-верификация за владельцем._
  - ~~🟢 _Остаток C (мелочь):_ привязка `ad_scheduled_posts.client_id`/`price` из composer'а планировщика.~~ Закрыто 2026-06-04: `ScheduleCreateIn` принимает `client_id`/`price`; `create_scheduled` пишет их в каждую отложку, авто-резолвит клиента из `source_ad_request_id` (бэкфилл строк) и мягко продвигает клиента в стадию `scheduled` (не понижает paid/published/lost). UI: поле «Цена размещения» в composer'е + заметка о привязке клиента в блоке «из заявки». +4 теста. Без миграции (колонки из 025 + FK из 027). Деплой — restart `setka` (web).

Открытые хвосты MVP:

- ~~**Офферные картинки кладёт владелец** в `web/static/ad_offers/` без UI~~ / ~~**правки текста в textarea не отправляются** (`/send` слал сохранённый `prepared_message`)~~ — закрыто 2026-06-02 (PR `feat/ad-cabinet-offer-library`): библиотека картинок с UI (`GET/POST/DELETE /api/ad-cabinet/offer-images` — загрузка/удаление/выбор чекбоксами в `/ad-cabinet`), `send` теперь принимает отредактированное тело письма (`message`) + выбранные картинки (`images`). Библиотека текстов = `message_templates` (CRUD на `/templates`, категория `ad_offer`). Картинки по-прежнему уходят community-токеном группы (R4) — без него оффер текстом.
- ~~🟡 **Пустые `reasons_json` при score из унаследованного `AdvertisementFilter`**~~ Закрыто 2026-06-03 (ветка `fix/ad-cabinet-empty-reasons`): когда пост помечается рекламой без накопленных причин (базовый фильтр пропустил пост — порог 4 > порога кабинета 3 — и предложка-сигналы не сработали), `classify` теперь добавляет причину «коммерческие признаки (score N)». Фикс в `modules/ad_cabinet/classifier.py` (без правок общего фильтра сводки и без дублирования его паттернов) + регресс-тест. Замечено было на заявке #1 (pizhanka, score 3, reasons `[]`).
- ~~🟢 **`can_message` не пречекается в scanner**~~ Закрыто 2026-06-03 ([PR #126](https://github.com/Valstan/setka/pull/126)): scanner прокачивает `can_message` для каждой НОВОЙ заявки (`messages_allowed` в потоке, только при rowcount>0 — рескан известных не дёргает VK), `/send` переиспользует свежий кэш (≤7 дней) вместо повторного VK-вызова.
- 🟢 **Фаза 2 (остаток)** — наборы офферных картинок **по регионам** + авто-send где `is_allowed=1` (под контролем оператора). ~~bulk-действия в инбоксе~~ закрыто 2026-06-03 ([PR #127](https://github.com/Valstan/setka/pull/127): мультивыбор + панель «статус/удалить» батч-запросом). Базовая библиотека картинок/текстов + выбор при сборке письма уже сделаны (см. выше).
- ~~🟢 **Фаза 3 (CRM)**~~ Закрыто — это дубликат **блока C** roadmap'а Кабинет 2.0 выше (`ad_clients`/`ad_payments`/`ad_publications`, миграция 027 + серия [#141](https://github.com/Valstan/setka/pull/141)/[#152](https://github.com/Valstan/setka/pull/152)–[#157](https://github.com/Valstan/setka/pull/157), страница `/ad-crm`). Построено и задеплоено 2026-06-04/05. Остаток — браузер-верификация владельцем.
- 🟢 **Фаза 4 (ML)** — заменить `classifier.classify` обученной TF-IDF/линейной моделью за тем же интерфейсом; разметка — накопленные `ad_requests` + исход оператора.
- 🟢 **Фаза 5** — авто-правила ответов, follow-up по расписанию, аналитика воронки detected→contacted→published→paid.

### Telegram-репосты (восстановлены 2026-06-02)

- ~~**Восстановить два потока репостов в Telegram (owner-request brain `2026-06-01`).**~~ Закрыто 2026-06-02 ([PR #102](https://github.com/Valstan/setka/pull/102) + fix [PR #103](https://github.com/Valstan/setka/pull/103), задеплоено на `6e5973b`, миграция 020). **Поток A** — сводки `mi` (все темы) → `@malmyzh_info` (AFONYA), хук в `parse_and_publish_theme`. **Поток B** — стена ВК `-218688001` → `@gonba_life` (VALSTANBOT), таска `mirror_community_to_telegram` + beat (мин. 10/40, 7–23), live-подтверждён (3 поста). Новые модули `modules/publisher/telegram_repost.py`(+`_config.py`), `modules/telegram_gonba_mirror.py`. Секреты в env (pool #008), в БД — канал+имя бота. Отчёт brain: `mailbox/to-brain/2026-06-02-telegram-reposts-restored.md`.
- ~~🟢 **Видео >50 MB / только-player VK-ролики не уходят в TG**~~ Частично закрыто 2026-06-03 ([PR #123](https://github.com/Valstan/setka/pull/123)): одиночное видео теперь при провале URL-отправки скачивается и шлётся файлом (`sendVideo` multipart, до 50 MB Bot API), при провале — degrade на текст вместо тихой потери. Только-player и >50 MB по-прежнему дропаются (degraded) — это потолок Bot API. Media-group остаётся на URL. **Остаток (player-only / >50 MB) — потолок Telegram Bot API, не чинится в принципе; снято с напоминаний 2026-06-03.**
- 🟢 **TG-заточенные хэштеги для каналов** — `clean_text_for_telegram` умеет добавлять, но off by default; включаются env `TELEGRAM_EXTRA_HASHTAGS_<CHAN>` (напр. `TELEGRAM_EXTRA_HASHTAGS_MALMYZH_INFO="Малмыж"`). По желанию владельца.
- ~~🟢 **UI per-community Telegram-таргет**~~ Закрыто 2026-06-03 (ветка `feat/ui-community-telegram-mirror`): на `/communities` добавлена колонка «TG-зеркало» с двумя inline-полями (`telegram_channel` + `telegram_bot`), редактируются прямо в таблице (паттерн как у name/category — onchange → PUT `/api/communities/{id}`). Пустая строка снимает зеркало (NULL). API: `CommunityUpdate`/`CommunityResponse`/`_community_to_dict` получили telegram-поля; апдейт-хендлер чистит пустую строку в NULL до общего цикла. +5 тестов. Без миграции (колонки из 020).

### Регионы и cross-region обмен новостями

- ~~🔴 **Новый РАЙОН молча выпадает из всех тематических волн (онбординг-баг).**~~ Закрыто 2026-06-02 (ветка `fix/raion-onboarding-bulletin-gate`): визард `/regions/new` создаёт запись в `regions`, но НЕ строку `region_configs` (её исторически создавала лишь Mongo-миграция), а гейт `run_all_regions_theme.config_gate` пускал регион только при наличии `region_configs` ИЛИ `bulletin_mode='communities'`. Итог: **Тужа** (raion, пул 49 communities) не публиковала **ничего**. Фикс: `config_gate` теперь пускает регион с **любым активным пулом communities** (`has_any_communities`) → район/область начинает публиковать сразу после засева пула, без ручной возни. Миграция 022 дала Туже брендированную строку `region_configs` + перекатегоризацию пула (`detsad`-свалка → union/sport/admin; `sosed`-чат → novost). +1 тест. **Авто-discovery `discovery-rolling-daily` отключён** (без нейро-фильтра ~98% мусора — на Туже из 136 авто-кандидатов годных ≈0).
  - 🟢 _Хвост:_ браузер-верификация первой публикации Тужи после деплоя; точечный добор пропущенных сельских источников через `/discover_communities` (длинный хвост СДК/библиотек).
- ~~**Регион «Кировская область Инфо» (kirov_obl) пустой — discovery не пополняет.**~~ Закрыто 2026-05-27 (этот PR): введена иерархия регионов `strana → oblast → raion` (миграция 015 с полями `regions.kind` + `regions.parent_region_id`). Создана запись `kirov_obl` с vk_group_id=-168170001 (https://vk.com/kirovskaya_info), 13 кировских районов привязаны через `parent_region_id`. Новый универсальный `modules/cascaded_bulletin.py` берёт по 5 свежих постов со стены главного сообщества каждого ребёнка, фильтрует рекламу/религию/дубли, публикует. Старая хрупкая логика «extract wall.refs из текста» удалена. Документация — `docs/REGIONS_HIERARCHY.md`.

- ~~**Cross-region обмен новостями («соседи репостят сводки друг другу») — мёртв.**~~ Закрыто 2026-05-28 ([PR #78](https://github.com/Valstan/setka/pull/78)): реанимирован **без дубляжа** — переиспользует движок `modules/cascaded_bulletin.run_cascaded_bulletin` с `source_mode="neighbors"`, тема `neighbors`, гейт `#Новости`. Источники — `Region.neighbors`. Тонкая обёртка `run_neighbor_bulletin`, задачи `share_neighbor_news`/`run_all_regions_neighbor_share`, beat `bulletin-share-neighbors-daily` (8:30). Мёртвый `modules/publisher/neighbor_sharing.py` удалён (один модуль). Тема `sosed` (парсинг `category="sosed"` внутри региона) не тронута.

- ~~**UI поле «соседи» отсутствует при создании/редактировании региона.**~~ Закрыто 2026-05-28 ([PR #79](https://github.com/Valstan/setka/pull/79)): multi-select «Соседи» в add/edit модалках на `/regions` (`web/templates/regions.html`), сохраняет коды в `Region.neighbors`. API уже поддерживал. _Браузер-верификация после деплоя ещё не сделана (см. SESSION_HANDOFF)._

- ~~**Bal/Kukmor — сироты без `parent_region_id` (Татарстан).**~~ Закрыто 2026-05-28 ([PR #77](https://github.com/Valstan/setka/pull/77)): миграция 016 создала `tatarstan_obl` (vk_group_id=-239149826, vk.com/tatar_stan_info), bal/kukmor привязаны. Beat-слоты `postopus-tatarstan-oblast-9/-19`. Для публикации нужен токен `COMM_239149826` (см. token routing ниже).

- ~~⏳ **kirov_obl переведён с каскада на собственный пул communities (community-mode).**~~ **Закрыто 2026-05-31** ([PR #95](https://github.com/Valstan/setka/pull/95), задеплоено + публикация подтверждена живьём `wall-168170001_3005`). Область собирает тематические сводки из своего пула (12 тем). **Критичный баг найден и пофикшен:** community-mode oblast выпадала из ВСЕХ тематических волн — гейт `run_all_regions_theme` требовал строку `region_configs`, а у kirov_obl её не было (только `regions.config->>'bulletin_mode'`). С 30.05 область не публиковала ничего (каскад снят, в волны не входит). Фикс: гейт пускает community-mode регионы без `region_configs` + миграция 018 (брендированные заголовки/хэштеги «Кировская область» по 12 темам). Тонкие пулы добраны через `/discover_communities` (sport 1→4, selhoz 2→5, zdorovie 2→3; пул 53→60).
- ~~**`tatarstan_obl` → community-mode**~~ Закрыто 2026-06-01 ([PR #100](https://github.com/Valstan/setka/pull/100)): миграция 019 (`bulletin_mode='communities'` + брендинг `#Татарстан16`), пул засеян 44 источниками через `/discover_communities` (11 тем, `promyshlennost` пуст). Первая публикация подтверждена вживую в 11:40-волне novost: `wall-239149826_9`. Токен `COMM_239149826` пользователь внёс через `/tokens`.
- ~~🟢 **kirov_obl `selhoz` — точечно добрать ВятГАТУ**~~ Снято с напоминаний 2026-06-03 (owner добавит вручную при необходимости — текущего пула хватает). _Контекст:_ (Вятский ГАТУ, агроуниверситет): в 2 прохода `/discover_communities` не всплыл чистым кандидатом (только принт-точка в нём). selhoz сейчас 5 (Минсельхоз, Агрофирмы КМК, сельхозтехникум, Управление ветеринарии, Россельхознадзор) — флагман-вуз был бы сильным добавлением. Резолвить хэндл напрямую (`groups.getById screen_name`).

- ~~🟢 **UI: дропдаун категорий `Community.category` не содержит новых тем** (proisshestviya/molodezh/nauka/promyshlennost/selhoz/zdorovie/zhkh/priroda).~~ Закрыто 2026-05-31 ([PR #96](https://github.com/Valstan/setka/pull/96)): `web/templates/communities.html` — `window.communityCategories` стал каноническим источником (14 тем), статические select'ы (фильтр + модалка) и `getCategoryLabel` заполняются из него (убрана 4-кратная дупликация, породившая дрейф). Discovery-страница (`region_discovery.js`) намеренно осталась на легаси-таксономии района.

- ~~**🐞 Баг Тужи: `tuzha.vk_group_id=239050321` положительный**~~ Закрыто 2026-05-31 ([PR #90](https://github.com/Valstan/setka/pull/90), задеплоено): оказался **не рантайм-багом** — весь publish/token-routing путь уже defensively нормализует знак (`VKPublisher._normalize_group_owner_id`=`-abs`, `TokenPolicy.pick(group_id=…)` берёт `abs`, `get_wall_posts(-abs(…))`), tuzha не публиковался в чужую группу. Чинился инвариант данных + root cause (не было нормализации на записи): миграция 017 привела данные (`tuzha` → `-239050321`, 0 положительных) + Pydantic-валидатор `_to_negative_owner_id` на `RegionCreate/Update` не даёт положительному id попасть в БД снова. +5 тестов.

### Discovery

- ~~🟡 **`regions.config.localities` загрязнены мусорными топонимами у части районов**~~ Снято с активных напоминаний 2026-06-03 (owner: топонимы чищу вручную при освежении районов через чат — авто-аудит по всем районам не заводим). _Контекст сохранён:_ (обнаружено 2026-06-02 при освежении `verhoshizhem`): в списке сёл встречаются «Москва», «Казань», «Свобода», «Песок», «Котельное», «Косино» и т.п. — это убивает locality-discovery (поиск тянет одноимённые города/коммерцию, ~98% шум). Нужен аудит `config.localities` по всем районам (особенно legacy Mongo-наследие) + чистка явных не-нп / городов-омонимов. Влияет и на `RegionalRelevanceFilter` (через `region_configs.localities`). См. журнал освежения (`docs/REGION_REFRESH_LOG.md`).
- ~~**Relevance-фильтр пропускает омонимные стемы**~~ Закрыто 2026-05-25 ([PR #44](https://github.com/Valstan/setka/pull/44), `a7bec89`): `_passes_relevance` с center-stem requirement + ≥2 distinct stems fallback + `_LARGE_GROUP_MEMBERS_THRESHOLD=50000` для крупных пабликов. 278 ложно-релевантных групп в БД для tuzha удалены SQL'ом.
- ~~**ChatGPT-prompt для localities — помечать омонимные нп**~~ Закрыто 2026-05-25 ([PR #47](https://github.com/Valstan/setka/pull/47), `d6249db`): prompt в `web/templates/region_prepare.html` теперь явно просит ChatGPT исключать топонимы, чьи названия совпадают с обычными русскими словами.
- ~~**Перевести `/api/discovery/trigger` на Celery + UI polls**~~ Закрыто 2026-05-25 ([PR #49](https://github.com/Valstan/setka/pull/49), `0edf84b`): endpoint возвращает `task_id`, UI polls `/api/discovery/task/{id}/status`. Worker через `tasks/discovery_tasks.run_discovery_for_region_async`.
- ~~**🟡 Groq API key 403 Forbidden** — кнопка «✨ AI-черновик» в `/notifications` возвращает ошибку~~ Закрыто 2026-06-06 (ветка `feat/notifications-ai-drafter-clipboard-fallback`): добавлен **clipboard-fallback** (тот же human-in-the-loop паттерн, что и discovery #41). Когда Groq недоступен (нет `GROQ_API_KEY` / SDK / quota-ошибка), `draft_comment_reply` отдаёт готовый `prompt` в ответе, фронт копирует его в буфер — оператор вставляет в свой ChatGPT/Claude, ответ вставляет назад в textarea. `_build_prompt`→публичный `build_draft_prompt` (alias сохранён). +4 теста. **Без миграции, деплой — restart `setka` (web).** _Остаток — нулевой:_ если когда-нибудь появится бюджет, просто внести `GROQ_API_KEY` в `/etc/setka/setka.env` + restart, и кнопка снова пойдёт через API (fallback включается автоматически только при отсутствии ключа).

### Token routing (2026-05-27)

- ~~**Valstan заблокирован VK до 2026-05-28T06:59:03**~~ Закрыто 2026-05-28: блокировка истекла, но токен оказался мёртв (пользователь сменил пароль аккаунта → токен инвалидирован). **Перевыпущен** новый через своё приложение `client_id=51421557` (scope `wall,groups,photos,docs,video,stories,pages,notifications,stats,market,offline`). Введён через `/tokens` (БД), синхронизирован в env, restart, `enable`, validate → `valid`. Парсинг и публикация снова на VALSTAN.
- ~~**kirov_obl публиковать не сможет** до создания community-token~~ Закрыто 2026-05-28: токен `COMM_168170001` (группа `vk.com/kirovskaya_info`) добавлен в БД, валиден. kirov_obl публикует через community-token.
- ~~**Парсинг и публикация читают токен из разных источников (env vs БД) — рассинхрон при ротации.**~~ Закрыто 2026-05-28 ([PR #76](https://github.com/Valstan/setka/pull/76)): `get_active_parse_tokens` берёт значение из БД (`vk_tokens`), а не env. Единый источник истины — управление через `/tokens`. Фильтр `validation_status != 'invalid'`. env `VK_TOKENS` — только аварийный DB-down fallback.
- ~~**`tatarstan_obl` ждёт community-token**~~ Закрыто 2026-06-01: токен `COMM_239149826` внесён через `/tokens` (валиден), область переведена в community-mode и публикует ([PR #100](https://github.com/Valstan/setka/pull/100), см. выше).
- ~~🟢 **`tatarstan_obl` — добрать `promyshlennost`**~~ Снято с напоминаний 2026-06-03 (owner добавит вручную — остальные 11 тем публикуют). _Контекст:_ (опц.) тема осталась пустой при засеве пула (чистого офиц. источника Татнефть/КАМАЗ корп. в проходе discovery не всплыло). Точечно резолвить хэндлы через `groups.getById screen_name` и засеять `seed_region_communities.py`. Не блокер — остальные 11 тем публикуют.
- ~~**Hot-fix: 3 hot-path'а парсинга не фильтровали disabled_until**~~ Закрыто 2026-05-27 ([PR #72](https://github.com/Valstan/setka/pull/72)). С момента блокировки VALSTAN ~10:00 все beat-таски брали первый токен из env (VALSTAN) и падали с VK error 5. Заменено на `get_active_parse_tokens(session)` в `tasks/parsing_scheduler_tasks.py:188-192`, `modules/cascaded_bulletin.py:310-313`, `modules/copy_setka_network.py:88-101` (убран опасный fallback на полный список). Подтверждено: после restart worker'а cascaded-таска собрала 55 постов вместо 0.
- 🟢 **wall.repost — единственная точка отказа на одном user-токене (VALSTAN).** Сейчас работает (VALSTAN перевыпущен 2026-05-28). **Разобрано с owner 2026-06-03 — оставляем как есть:** (а) путь «2-й user-токен в `VK_PUBLISH_TOKEN_NAMES`» **закрыт** — у владельца нет второго личного VK-аккаунта (community-токены `wall.repost` не умеют, VK error 27); (б) переписать `copy_setka` на `wall.post`-копию = **даунгрейд** (теряется нативная VK-атрибуция «репост из X» + обратная ссылка), делать не будем. Если VALSTAN однажды умрёт — быстрый фикс прежний: перевыпустить токен (как 2026-05-28). Не срочно, в работу не берём.
- ~~🟢 **UI чекбокс «использовать для публикаций»** прямо в `/tokens`~~ Закрыто 2026-06-03 (ветка `feat/tokens-publish-role`): миграция 023 (`vk_tokens.role`), на `/tokens` у каждого user-токена свитч «Использовать для публикаций» → `POST /api/tokens/{name}/publish-role`. `TokenPolicy.pick` объединяет env-whitelist с токенами, у которых `role='publish'` в БД — **аддитивно** (нулевая регрессия env-поведения), hard deny-list (`VK_NEVER_PUBLISH_TOKEN_NAMES`) сохраняет приоритет. Community-токены → 400 (публикуют в свою группу независимо). +6 тестов.

### Прочее

- ~~**`logs/app.log` не пишется с 2026-05-22**~~ Закрыто 2026-05-25: разбор показал, что app.log не был «сломан» — `FileHandler` работал, файл был открыт, но `LOG_LEVEL=WARNING` в `/etc/setka/setka.env` отсекал 99% событий, а WARNING'ов с тех пор просто не было (`metrics_middleware` slow-request threshold 1.0s, а `/api/health/full` стабильно отдаёт ~1.01s). Параллельно содержимое app.log 100% дублировалось в `uvicorn_production.log` через systemd `StandardOutput/Error=append:`. Решение — убрать FileHandler из `main.py` полностью, оставить единственный канал через stderr → systemd-редирект → `uvicorn_production.log`. Дефолт `LOG_LEVEL` поднят с `WARNING` до `INFO`. На проде убран `LOG_LEVEL=WARNING` из `setka.env`, старый `app.log` архивирован. Doc-ссылки на `app.log` обновлены во всех `.md` / `.claude/commands/`.

### Git / brain_matrica integration

- ~~**Branch protection rules на GitHub для `main`.**~~ Закрыто 2026-05-23 (см. `DEV_HISTORY.md`): PR required (0 approvals) + CI `test (3.12)` required + strict + no force push + no deletion + enforce_admins. Конфиг в `scripts/branch-protection.json`. Hot-fix runbook — в `docs/OPERATIONS.md` §8.

### Прод-доступ

- ~~**SSH alias `setka-prod` vs `setka`.**~~ Закрыто 2026-05-23 (см. `DEV_HISTORY.md`): sweep по 13 файлам — `CLAUDE.md`, `.claude/settings.json`, `.claude/commands/{start,check,celery,logs,sql,reliz,finish}.md`, `.gitignore`, `database/migrations/README.md`, `scripts/migrate.py` — везде `setka-prod` → `setka`. `docs/DEV_HISTORY.md` и закрытые техдолги в этом файле не тронуты (исторические записи).

### Запуск и окружение

- ~~**`main.py:25` хардкодит `/home/valstan/SETKA/logs/app.log`.**~~ Закрыто ранее: `main.py:45` уже использует `os.getenv("LOG_PATH", "/home/valstan/SETKA/logs/app.log")` с safe-fallback на StreamHandler. Запись была устаревшей.
- ~~**`venv` создаётся вручную в каждом worktree.**~~ Закрыто ранее: есть `scripts/setup-dev.ps1` и `scripts/setup-dev.sh`. 2026-05-22 добавлено `pre-commit install` в оба скрипта — теперь свежий worktree сразу получает git-хук.
- ~~**`scripts/setup-dev.ps1` хардкодит `py -3.11`**~~ Закрыто 2026-05-23 (см. `DEV_HISTORY.md`): fallback `py -3.11 → -3.12 → -3` по аналогии с `setup-dev.sh`. Заодно добавлен UTF-8 BOM (PS 5.1 без BOM путал encoding на русской локали).
- ~~**Хардкоды `/home/valstan/SETKA/logs/parser*` в `web/api/parsing.py` и `tasks/parsing_tasks.py`.**~~ Закрыто 2026-05-23 (см. `DEV_HISTORY.md`): введён env `SETKA_LOGS_DIR` (default `/home/valstan/SETKA/logs`), `OUTPUT_DIR`/`REPORTS_DIR`/`VIDEO_REPORT_PATH` вычисляются от него, `_init_logger` safe-fallback'ит на StreamHandler при недоступном пути. В `web/api/parsing.py` удалён неиспользуемый дубль `OUTPUT_DIR`. +5 тестов в `tests/test_tasks/`.

### Документация / разработка

- ~~**Шаблон записи `DEV_HISTORY.md`**~~ Закрыто ранее: шаблон в шапке `DEV_HISTORY.md` (раздел «Правила записи» + collapsible шаблон).
- ~~**Pre-commit и CI разъезжаются**~~ Закрыто ранее: `.pre-commit-config.yaml` фиксирует `default_language_version.python: python3.11` для всех хуков. Прод (3.12) и линтеры (3.11) дают одинаковый стиль.
- ~~**Доочистка legacy flake8-ошибок**~~ Закрыто 2026-05-23 за 3 PR (см. `DEV_HISTORY.md` «Legacy flake8 cleanup PR 1/2/3»):
  - **PR #17** — E712 (47), F841 (18), W291 (16), E722 (2), F601 (2 + **реальный баг** в фильтре рекламы), F811 (2 + **реальный баг** в `/api/system_monitoring/live`) — всего ~88 правок и 2 найденных runtime-бага.
  - **PR #18** — E501 (96 строк) → `# noqa: E501`.
  - **PR #19** — E402 (147 импортов) → `# noqa: E402`.
  - `extend-ignore` обрезан с `E203,W503,E402,E501,E712,F841,W291,E303,E722,F601,F811,E302,W391,F541` (14 кодов) до `E203,W503` (только black ↔ pep8 конфликт). Все новые нарушения flake8 теперь падают в pre-commit.
- ~~**Отслеживать F601-фикс в фильтре рекламы**~~ Закрыто 2026-05-26: ratio стабилизировался. Замеры за 3 окна (0.347 % → 0.600 % → **0.54 %** за последние 100 succeeded-tasks 2026-05-25 21:21 → 2026-05-26). Колеблется в коридоре 0.35-0.60 %, далеко от тревожного порога 1.5 %. PR [#35](https://github.com/Valstan/setka/pull/35) фиксировал нитку ещё 2026-05-24, эта запись была вторичным мониторингом. Решение оставить вес price-patterns=2.
- ~~**Инкрементально ломать длинные строки, помеченные `# noqa: E501`**~~ Закрыто 2026-05-24 (см. `DEV_HISTORY.md` «Break long lines PR #1-#4»). За 4 атомарных PR в один день: PR #1 (`modules/system_status_notifier.py`, 15), PR #2 (`tasks/parsing_tasks.py`, 10), PR #3 (`tasks/vk_carousel_tasks.py` + `modules/service_activity_notifier.py`, 8), PR #4 (остальные 40 файлов, 63). **В проекте 0 строк с `# noqa: E501`**. Поведение функций не менялось (Python склеивает adjacent string literals на этапе компиляции).
- ~~**Рефакторинг `scripts/*` через `pyproject.toml` + `pip install -e .`**~~ Закрыто 2026-05-24 (см. `DEV_HISTORY.md` «pyproject.toml + editable install»). Создан `pyproject.toml`, добавлен `pip install -e .` в CI и `scripts/setup-dev.{sh,ps1}`. Из 53 файлов удалён `sys.path.insert(0, ...)` и ~50 связанных unused-импортов; ~90 `# noqa: E402` снято. Осталось ~30 legit-кейсов noqa: E402 (logging.basicConfig / os.environ.setdefault до импортов в scripts/tests). **Прод-action item**: разовый `ssh setka 'cd /home/valstan/SETKA && ./venv/bin/pip install -e .'` после merge.
- ~~**Покрыть тестами восстановленные F821-ветки**~~ Закрыто 2026-05-23 (см. `DEV_HISTORY.md`): +14 тестов в `tests/test_core/test_context_factory.py` (3), `tests/test_utils/test_retry_utility.py` (6), `tests/test_utils/test_text_utils.py` (5). Покрыты `ContextFactory.create_from_region`, `retry_with_fallback`, `retry_with_circuit_breaker` (+`CircuitBreaker` сценарии заодно), `truncate_text` (+ integration через `TextOnlyBulletinBuilder.build_bezfoto_bulletin`). Итого 379/379 зелёных.

### Прод-операции

- ~~**Применение SQL-миграций — ручное.**~~ Закрыто 2026-05-22: миграция 010 + `scripts/migrate.py` (stdlib, через `sudo -u postgres psql`). Сверяется с `applied_migrations`, применяет недостающее в транзакции вместе с INSERT-ом. Использование: `ssh setka-prod 'cd /home/valstan/SETKA && python3 scripts/migrate.py up'`.
- ~~**GRANT в миграциях / ALTER DEFAULT PRIVILEGES.**~~ Закрыто 2026-05-22 миграцией 009 (см. `DEV_HISTORY.md`). Будущие миграции не должны включать explicit `GRANT ALL ... TO setka_user` — default privileges выдаст их автоматически.
- ~~**Auto-mode classifier блокирует SSH на прод.**~~ Закрыто 2026-05-22: `.claude/settings.json` с `permissions.allow: ["Bash(ssh setka-prod:*)"]` (закоммичен в репо через `!.claude/settings.json` в `.gitignore`). Destructive-операции по-прежнему через `AskUserQuestion` — это политика CLAUDE.md, не permissions.

---

## 🟢 Идеи

### 🎙 Модуль аудио → текст (исследование 2026-06-22 → делегировано Мозгу)

`⏱ 2026-06-22 · snooze 0 · РЕШЕНО 2026-06-22 — в Сарафане не строим, отдано Мозгу как скил`

Запрос владельца: интерфейс в Сарафане — кидаешь ogg/mp3, получаешь распознанный
текст, копируешь в буфер. Исходно хотел слать куски в `@smartspeech_sber_bot` и
забирать текст. **Кода нет — только исследование развилок.**

**Установленные факты (чтобы не передоказывать):**
- ❌ **Telegram бот боту не пишет** (Bot API) — автоматизировать `@smartspeech_sber_bot`
  можно только userbot'ом (Telethon, личный аккаунт) → серая зона ToS / риск бана.
  Владелец от этого отказался.
- ⏰ **SaluteSpeech Freemium для физлиц закрывается 15.07.2026** — бесплатного пути
  через сам сервис Сбера не будет (платное — юрлица/постоплата).
- ✅ **Лучшее бесплатное распознавание русского — open-source self-host:** **GigaAM v3**
  (Сбер, `pip install gigaam`, ~3.3% WER, CPU-ок, обгоняет Whisper) или Whisper
  (универсальнее, ~6–8% WER). Длинные файлы берут целиком — резать на куски не нужно.
- 🧱 **Трилемма** (получить можно любые 2 из 3): «открыть с любого компа без установки» /
  «без нагрузки на сервер» / «качество GigaAM». Веб-страница, отданная сервером, **не
  может** сама поставить и запустить нативный код на произвольном компе (песочница браузера).
- **Варианты:** A — считает VPS (грузит слабый CPU сервера); **B — локальный помощник**
  (ставится 1× на комп, localhost-UI или мост к странице Сарафана; лучшее качество, без
  нагрузки на сервер — рекомендован под 2 машины владельца); C — в браузере через WASM
  (Whisper-small, ноль установки везде, качество ниже).

**Резолюция 2026-06-22 (решение владельца):** в Сарафан **не встраиваем**. Идея отдана
**Мозгу** как самостоятельный скил — локальный скрипт с графической оболочкой (Вариант B
без привязки к серверу: ставится 1× на машину, GigaAM v3, копирование текста в буфер).
Письмо `mailbox/to-brain/2026-06-22-audio-to-text-local-skill-idea.md` (`kind: idea`,
suggest). Реализация — на стороне Мозга. Факты выше оставлены как справка, если тема
когда-нибудь вернётся в SETKA-контур.

### Квартальный стратегический самоосмотр (#036, триггер 2)

`⏱ 2026-06-10 · snooze 0 · parked (до Q3 2026, авг–сен — календарное условие, не гниёт)`

Раз в квартал — отдельная сессия → письмо в `mailbox/to-brain/`: (а) рефакторинг-предложения с грубой стоимостью; (б) идеи развития функциональности владельцу. Решения за владельцем. Первый — Q3 2026.

### Пакет браузер-верификаций владельцем

`⏱ копится с 2026-06-03 · snooze 3+ · 2026-06-13 бо́льшая часть проверена ассистентом через Claude-for-Chrome под логином владельца — остаток ждёт реальных данных.`

**✅ Проверено 2026-06-13 (Claude-for-Chrome, операторский логин):** auth-гейт Ф0.1 (`/`→`/login`),
тёмная тема, ad-CRM С1 (4 вкладки `/ad`), С2 (поля «Снять через, дней»/«…или дата снятия» в
планировщике + в модалке «Оформить»), С3 (счётчик «просмотров всего» в воронке), С4 (плашка
«должников (>3 дн.)» + тумблер «Только должники»), С5 (модалка «Оформить заявку» собирает цепочку —
сабмит не жали), Статистика (графики); tiered-поиск #035 (`/communities` substring + RU↔EN-раскладка,
`/subscriber-growth` подсветка `<mark>`); кнопки «Σ область»/«без дублей»; `/posts` (3 фильтра); `/monitoring`
(Heartbeat + Liveness Celery pong/жив + CPU/Память/Диск); `/tokens` (счётчики 2/17/5/24 — фикс сошёлся);
радар-Ф0 (`/radar` Лента/Архив/Источники VK·TG·RSS + 🔔); DM-роутер (`/notifications` блок «Непрочитанные
сообщения» + кнопка ответа).

**🟢 Остаток (нужны реальные данные / наружу-действия — за владельцем):** С3 «Обновить просмотры»/«Отчёт
клиенту» (сейчас 0 публикаций), С2 «снять DATE» в списке запланированного (пусто), push-колокольчик
радара (нужен браузерный grant + новый пост в источниках). Сами наружу-действия (Оформить→разместить,
отправка ЛС, создание отложки, tuzha re-discovery) ассистент намеренно не выполнял.

Исходный UI-чек-лист (для справки) — UI-шаги (кнопки в браузере, не код): ad-cabinet серии [#152](https://github.com/Valstan/setka/pull/152)–[#157](https://github.com/Valstan/setka/pull/157) (таймлайн/оплаты-должники/заказы/чат/графики), DM-роутер Этапы 1-2 ([#164](https://github.com/Valstan/setka/pull/164)/[#165](https://github.com/Valstan/setka/pull/165): не-рекламные ЛС в уведомлениях, ответ из приложения, тред переписки), планировщик отложки B1/B2 (создать пост → проверить в VK-«Отложенных»), CRM `/ad-crm`, `/subscriber-growth` (+кнопки «Σ область»/«без дублей»), тёмная тема, `/publications`, `/monitoring` (heartbeat-таблица/liveness/кнопки управления), счётчики `/tokens`, smoke `tuzha` (`/regions/tuzha/prepare` → re-discovery → ai-batch, см. ⏳ Итерация 3), **tiered-поиск #035** (на `/communities`, `/posts`, `/subscriber-growth`, `/ad-crm` набрать кусок из середины слова / опечатку / запрос в EN-раскладке / номер с дефисами — должно находить и подсвечивать).

### Наблюдаемость / UI

- ~~**Счётчики «Главные/Вспомогательные токены: 0» на `/tokens`**~~ Закрыто 2026-06-07 (ветка `fix/tokens-main-aux-counters`): плашки были жёстко зашиты в `0` с устаревшим комментарием «Token type is not stored in DB model» — хотя `community_id` есть в модели с миграции 007. Введена чистая `web/api/token_management.compute_token_stats()` (main = валидные user-токены, aux = валидные community-токены `COMM_*`, broken = любые невалидные; разбиение `main+aux+broken==total`) + эндпоинт `GET /api/tokens/stats`; `updateStatistics` в `tokens.html` дёргает его (клиентский fallback при сбое). +6 тестов. Без миграции, деплой — restart `setka` (web). _Браузер-верификация за владельцем._

### Удобство разработки

- **`/check`** — health-check одной кнопкой (pytest + prod systemd + curl + Celery). _(Сделано — см. [`.claude/commands/check.md`](../.claude/commands/check.md).)_
- **`/celery`** — состояние Celery: workers, beat, последние публикации, Redis cooldown. _(Сделано — см. [`.claude/commands/celery.md`](../.claude/commands/celery.md).)_
- **`/logs`** — параметризованный просмотр прод-логов. _(Сделано — см. [`.claude/commands/logs.md`](../.claude/commands/logs.md).)_
- **`/sql`** — psql на проде с подтверждением. _(Сделано — см. [`.claude/commands/sql.md`](../.claude/commands/sql.md).)_
- ~~**Скрипт `scripts/dev-doctor.sh`** проверяет окружение~~ Закрыто 2026-06-03 (ветка `chore/dev-doctor`): read-only доктор — Python 3.11/3.12/3, venv + версия, импорт fastapi/celery/sqlalchemy/pytest, editable-install (`import modules`), pre-commit git-хук, psql, git-sync (делегирует `git_sync_check.sh`), best-effort SSH-probe прода (`--no-prod` чтобы пропустить). Exit 1 при FAIL, 0 при WARN.
- ~~**Hook на `git commit`**, который проверяет качество commit message~~ Закрыто 2026-06-03 (ветка `chore/commit-msg-hook`): `scripts/check_commit_msg.py` (stdlib-only) подключён `commit-msg`-стейджем в `.pre-commit-config.yaml` (+ `default_install_hook_types: [pre-commit, commit-msg]`, чтобы `pre-commit install` ставил оба типа). Проверяет Conventional Commits subject + обязательное тело для `feat`/`fix`/`refactor`; пропускает Merge/Revert/fixup/squash. +15 тестов. **Существующим dev-машинам** нужен разовый `pre-commit install` для активации commit-msg-хука.
- ~~**Smoke-test после деплоя** — отдельный шаг в `/reliz`.~~ Закрыто 2026-06-05 (ветка `feat/reliz-smoke-test`): `scripts/smoke_test.py` (stdlib-only urllib) поверх seam'а `parse_and_publish_theme(dry_run=True)` ([PR #122](https://github.com/Valstan/setka/pull/122)) — ставит diagnostics-задачу эталонного региона (`POST /api/regions/{code}/diagnostics`), опрашивает по `task_id`, проверяет `success` + `posts_parsed >= --min-posts`, возвращает exit 0/1/2. Подключён как **Шаг 8.5** в [`/reliz`](../.claude/commands/reliz.md) (после рестарта, под `AskUserQuestion`, пропускается для деплоев без рестарта worker/beat). Чистая логика в `evaluate_result`; +13 тестов (`tests/test_scripts/test_smoke_test.py`). Применение: `ssh setka "cd /home/valstan/SETKA && ./venv/bin/python scripts/smoke_test.py"`.

### Наблюдаемость

- ~~**Cross-process rate-limit на VKClient**~~ Закрыто 2026-05-26: `modules/vk_monitor/rate_limiter.py` с двумя backend'ами (ThreadingRateLimiter default, RedisRateLimiter через Lua-script с PEXPIRE). Selection через env `VK_RATE_LIMIT_BACKEND=redis|threading`. Graceful fallback на threading при недоступном Redis. +8 тестов.
- ~~**Дашборд «состояние сводок»**~~ Закрыто 2026-05-26: Prometheus + Grafana стек, дашборд `SETKA — состояние сводок` (4 панели: heatmap часов с публикации, stat-плашка простаивающих регионов, темп публикаций, pie долей по темам). Метрики: `setka_digest_published_total{region,topic,result}` + `setka_digest_last_published_timestamp{region,topic}`. Установка: `scripts/setup-monitoring.sh`. Доступ через SSH tunnel. См. `monitoring/README.md`.
- ~~**Multiprocess metrics для worker'а**~~ Закрыто 2026-05-26: `track_digest_published` вызывается из Celery worker'а, а `/metrics` живёт в web — без shared backend счётчики из worker'а до Prometheus не доходят (дашборд оставался пустым). Поднят `PROMETHEUS_MULTIPROC_DIR=/var/lib/setka/prom_multiproc` + `MultiProcessCollector` в `monitoring/metrics.py`; `digest_last_published_timestamp` Gauge получил `multiprocess_mode='max'`, остальные — `'livesum'`. `setup-monitoring.sh` создаёт каталог + drop-in `setka.service.d/prometheus-multiproc.conf` (то же для celery-worker). Celery worker_shutdown hook вызывает `mark_process_dead(pid)`. +4 теста.
- ~~**`setka_digest_published_total` остаётся пуст несмотря на успешные публикации.**~~ Закрыто 2026-05-28 ([PR #75](https://github.com/Valstan/setka/pull/75)): убран `ExecStartPre=/bin/rm -rf $PROM_MULTIPROC_DIR` из шаблона drop-in (`scripts/setup-monitoring.sh`) + из обоих прод-drop-in'ов `/etc/systemd/system/setka{,-celery-worker}.service.d/prometheus-multiproc.conf` + daemon-reload + restart. Каталог общий — `rm -rf` при рестарте любого сервиса сносил mmap другого. Очистку stale-PID делает `mark_process_dead` в worker_shutdown hook. Прод-проверка: файлы метрик пережили рестарт. `gauge_max_*.db` появится после первой реальной публикации. _Исходная диагностика 2026-05-26/27 ниже._
  <details><summary>Исходная диагностика</summary>Обнаружено 2026-05-26 сразу после релиза multiproc-фикса; **обновлено 2026-05-27** после расследования в smoke-сессии. Прямой smoke-test на проде (`./venv/bin/python -c "from monitoring.metrics import track_digest_published; track_digest_published(...)"`) **работает** — counter и Gauge инкрементируются, `gauge_max_*.db` создаётся. Cascaded-таска через celery worker (после hot-fix PR #72) тоже завершалась с `posts_published > 0` и явным `pub.success=False` (VK error 10) → код-path с `track_digest_published(result="failed")` точно выполнялся. Но в `/var/lib/setka/prom_multiproc/` после рестарта worker'а есть только `counter_<worker_pid>.db` + `gauge_livesum_<worker_pid>.db`; **нет ни одного `gauge_max_*.db`** (для `digest_last_published_timestamp`, mode='max'), и `curl /metrics | grep setka_bulletin_` пусто. **Корневая гипотеза**: drop-in для `setka-celery-worker.service` имеет `ExecStartPre=/bin/rm -rf /var/lib/setka/prom_multiproc` (создан `scripts/setup-monitoring.sh`), который **сносит весь каталог при каждом restart worker'а** — включая файлы web-процесса. Поскольку оба сервиса делят один и тот же каталог через тот же drop-in, любой restart обнуляет состояние. Фикс: убрать `rm -rf` из drop-in (он создавался для очистки stale-файлов, но `mark_process_dead(pid)` в worker_shutdown hook уже эту работу делает корректно). **Файлы**: `scripts/setup-monitoring.sh:?`, `/etc/systemd/system/setka.service.d/prometheus-multiproc.conf`, `/etc/systemd/system/setka-celery-worker.service.d/prometheus-multiproc.conf`. Старые ссылки на `modules/kirov_oblast_bulletin.py:438,487` устарели — после PR #70 трекинг в `modules/cascaded_bulletin.py:454+` (block при `pub.success`).</details>
- ~~**Алёрт в Telegram-бот**, если за последние 6 часов ни один регион не выпустил `novost`-сводка~~ Закрыто 2026-06-03 (ветка `feat/bulletin-heartbeat-watchdog`): надёжный Redis-heartbeat `setka:digest_last_published:<topic>` пишется из единой точки `track_digest_published` (Prometheus-gauge на проде ненадёжен — multiproc-mmap); beat-watchdog `check_bulletin_heartbeat` (раз в час 10:00–22:00) шлёт Telegram-алёрт при протухании `novost` дольше 6ч (cooldown 6ч). `None`-heartbeat не алёртит (свежий деплой ≠ слом). `modules/bulletin_heartbeat.py` + 10 тестов.
  - ~~🔴 **Heartbeat #018 молча НЕ писался на проде → watchdog был мёртв с 2026-06-03.**~~ Закрыто 2026-06-05 (цепочка PR #146→#147→#148, реальный корень — в #148). Вскрыто новым дашбордом (`/api/monitoring/heartbeat` всегда `unknown:no-heartbeat`, хотя сводки публикуются 6×/сутки; сам watchdog в логе докладывал `unknown:no-heartbeat`). **Реальный корень (#148):** `VKPublisher.publish_bulletin()` возвращает **dict** `{"success": bool, …}`, а три call-site (`parsing_scheduler_tasks` regular+mourning, `cascaded_bulletin`) обращались к `publish_result.success` как к **атрибуту объекта** → `AttributeError: 'dict' object has no attribute 'success'` при вычислении аргумента **до** вызова `track_digest_published`. Исключение глушилось обёрткой вызывающего на `debug` (невидимо при прод `LOG_LEVEL=INFO`) → трекинг не вызывался → heartbeat не писался. Изолированные пробы звали `track_digest_published` напрямую с `result="success"`, минуя битый доступ, потому баг и прятался. **Путь к корню:** #146 (heartbeat перед Prometheus — гипотеза не та), #147 (fork-safe Redis PID-guard + **перевод всех немых `debug`-обёрток на `warning`** — именно это вскрыло реальный traceback на проде), #148 (хелпер `monitoring.metrics.publish_result_label()` сводит dict/объект к `"success"`/`"failed"`; три call-site переведены на него). **Подтверждено вживую 2026-06-05 11:33:** ключи `setka:digest_last_published:{addons,union}` появились, WARNING исчезли. Полезные побочки сохранены: heartbeat пишется первым/независимо от Prometheus (#146), Redis fork-safe (#147), сбои громкие WARNING (#147). +6 тестов суммарно. **Деплой:** restart `setka-celery-worker` + `setka-celery-beat`, без миграции. **Урок:** «best-effort + `debug`-глушилка» спрятала сломанную фичу на дни — сбои наблюдаемости должны логироваться видимо.
- ~~**Структурированные логи** — Celery worker пишет plain-text.~~ Закрыто 2026-06-03 ([PR #121](https://github.com/Valstan/setka/pull/121)): stdlib `JSONFormatter` (`utils/json_logging.py`) + опт-ин через env `LOG_FORMAT=json` (дефолт text — нулевая регрессия прода), переустановка на `worker_ready`. Включение на проде: `LOG_FORMAT=json` в `/etc/setka/setka.env` + restart (без новых зависимостей).
- ~~**Веб-дашборд управления/здоровья (owner-request brain `2026-06-04`, idea #1).**~~ Закрыто 2026-06-05 (ветка `feat/obriv-command`): **probe-before-build** показал, что `/monitoring` уже покрывает ~60% запроса (статус системы, CPU/mem/disk, операции, состояние сводок, статус регионов), поэтому вместо новой страницы — **расширение `/monitoring`** реальной дельтой. Добавлено: (а) **💓 Heartbeat сводок** — вывод `bulletin_heartbeat` (#018) в UI (раньше только Telegram-алёрт): новый `bulletin_heartbeat.all_heartbeats()` (скан Redis, фильтр cooldown-ключей) + `GET /api/monitoring/heartbeat` (per-topic fresh/stale/unknown, `_HEARTBEAT_FRESH_HOURS=26` для дисплея + строгий 6ч-watchdog по `novost`); (б) **🫀 Liveness Celery** — `GET /api/monitoring/liveness` (`inspect.ping()` воркеров + инференс beat по `novost`-heartbeat); (в) **🎛️ Ручное управление** — скан региона + стоп workflow (переиспользуют `/api/test-workflow/*`, под `confirm()`). +10 тестов. **Деплой:** только restart `setka` (web), без миграции. Ack владельцу взят (`mailbox/to-brain/2026-06-05-web-dashboard-thread-ack.md`). 🟢 _Остаток:_ браузер-верификация владельцем (heartbeat-таблица, ping воркеров, кнопки управления); доклад brain о результате (рефлекс #009). AI-дедуп новостей остаётся отложенным (локальные embeddings, нулевой бюджет — см. mailbox brain `2026-06-04`).

### Продукт

- ⏳ `⏱ 2026-06-05 · snooze 0 · watch (R3 ждёт ≥1-2 недель снимков — готов ~2026-06-14+; линия «без дублей» появилась после пн 05:30 MSK 2026-06-08)` **Интерактивный мульти-график роста подписчиков по сообществам** (owner-request 2026-06-05 / brain-директива 2026-06-06 recommend). Один Chart.js со многими линиями (по линии на сообщество) + чекбоксы-переключатели внизу (мульти-выбор). **R1 фундамент готов 2026-06-06** (PR #161): (1) ✅ миграция 031 `community_member_snapshots` + ORM `CommunityMemberSnapshot`; (2) ✅ суточная beat-таска `collect-member-snapshots-daily` (04:00 MSK), `modules/members_snapshot.collect_member_snapshots`, upsert по `(community_id, snapshot_date)`. **R2 UI готов 2026-06-06** (ветка `feat/subscriber-growth-compare-chart`): страница `/subscriber-growth` (нав «Контент»), `web/api/subscriber_growth.py` (`GET /communities` свод latest/first/delta/laggard + `GET /series?ids=&days=` мульти-серия с единой осью дат, gap-as-null), чистые хелперы `build_series`/`summarize_communities` (+11 тестов), Chart.js + чекбоксы/поиск/окно/«Топ-5»/«Отстающие». R4-lite: бейдж `is_laggard` (≥2 точки ∧ delta≤0). **R-probe `stats.get` выполнен** (`scripts/probe_stats_get_capability.py`): VK отдаёт reach/visitors **только админ-токену своих ~17 групп** (community-токен → [27], чужие → [15]) → как сравнение по всем сообществам просмотры непригодны, MVP по подписчикам. **Деплой R2:** только restart `setka` (web), без миграции. **R-scope (2026-06-07, owner): учёт сужен до ГЛАВНЫХ ИНФО-групп регионов** (`regions.vk_group_id`, куда выпускаем сводки), а не весь пул ~840 сообществ — снимать всё жгло VK API ради групп, которые не сравниваем. Миграция 033 заменила per-community `community_member_snapshots` → per-region `region_member_snapshots` (DROP старой, ORM `RegionMemberSnapshot`); `collect_member_snapshots` перебирает 16 активных регионов (1 batch вместо ~2×500); API `GET /subscriber-growth/regions` + `/series` отдают регионы; UI «Сообщества»→«Регионы». **Деплой R-scope:** миграция 033 (с DROP — destructive, под #025) + restart web/worker/beat. **R-oblast (2026-06-07, owner-request, [PR #184](https://github.com/Valstan/setka/pull/184), задеплоено на `8af6bb9`):** список под графиком сгруппирован по областям (Кировская/Татарстан отдельно) + сортировка по подписчикам; кнопки-агрегаты «Σ область» (сумма главных групп области по датам, районы+областная группа, с дублями) и «область без дублей» (уникальные через union `groups.getMembers`). Миграция 034 `oblast_unique_member_snapshots` + `modules/oblast_unique_members.py` + beat `collect-oblast-unique-snapshots-weekly` (пн 02:30 UTC / 05:30 MSK); дедуп только по ~16 главным группам (нагрузка ничтожна). API `/regions` (+oblast-группировка) и `/series` (+`oblast_sum`/`oblast_uniq`). **Деплой:** миграция 034 (CREATE TABLE) + restart web/worker/beat — выполнено. 🟢 _Остаток:_ браузер-верификация владельцем; **линия «без дублей» появится после первого ночного дедупа — пн 05:30 MSK** (до этого кнопки disabled, `latest_unique=null`). **R3 — еженедельный авто-анализатор динамики (ранжирование + отстающие по медиане/квартилю) — ОТЛОЖЕН до накопления ≥1-2 недель снимков**; опц. апгрейд «просмотры своих групп» отдельной ниткой.
- ✅ **Near-dup дедуп переписанных новостей — улучшен 2026-06-07** (ветка `feat/near-dup-jaccard-dedup`). **Поправка к записи ниже:** SimHash near-dup **уже был подключён** не в `detector.py`, а в `advanced_parser.py` (`_is_near_duplicate_text`, порог 0.90, Хэмминг ≤12, гейт по длине) — работает в обоих путях сводки. Что добавлено: (а) **env-тюнинг** порога/корзины/гейта (`BULLETIN_SIMILARITY_THRESHOLD`, `BULLETIN_SIMHASH_BUCKET_GATE`) — дефолты прежние, нулевая регрессия; (б) **intra-batch Jaccard** по множеству слов (`text_token_set`/`jaccard_similarity` в `fingerprints.py`) — ловит **переставленные/переписанные** пересказы одной новости в пределах сводки («5 пабликов = одна новость»), которые char-SimHash упускает; ON by default, консервативный порог `BULLETIN_JACCARD_THRESHOLD=0.85`, `BULLETIN_JACCARD_MIN_TOKENS=10`, мгновенно отключается env; (в) диагностика — счётчики `near_dup_simhash`/`near_dup_jaccard` в stats + INFO-лог на каждый Jaccard-drop. +9 тестов (1094 зелёных). **Деплой — `/reliz`** (без миграции, restart worker/beat). _Калибровка порогов — по данным PoC курации (`/curate` класс «перефраз-дубль») + INFO-логам._ **Семантический дедуп тяжёлого перефраза (синонимы/др. цифры) по-прежнему за embeddings ниже.**
- ⏸ `⏱ 2026-06-04 · parked (до апгрейда VPS ≥4 ГБ RAM + swap — решение владельца 2026-06-07)` **AI-дедуп новостей (смысловое схлопывание дублей) — ОТЛОЖЕНО до апгрейда VPS** (решение владельца 2026-06-07). Цель: «5 сообществ запостили одну новость → в сводка идёт одна». **Probe-before-build (#020) 2026-06-07 дал жёсткие факты:**
  - **Нейро-embeddings (e5/LaBSE/MiniLM через torch) на текущем VPS невозможны:** RAM **1.5 ГБ всего** (свободно ~900 МБ), **swap=0**, **1 ядро**; web+worker+beat+postgres+redis уже едят ~600 МБ. torch-рантайм + модель = ~0.5–1 ГБ RSS → OOM-kill живого worker'а. Путь из письма brain 2026-06-04 («локалка на CPU») рассчитан на более жирный сервер. **Действующий потолок: для локальных embeddings нужен VPS с ≥4 ГБ RAM + swap.**
  - **Текущий дедуп ловит только ТОЧНЫЕ отпечатки** (`modules/deduplication/detector.py`): `lip` (id поста), media (id фото/видео), хэш текста, хэш «ядра» (середина 20–70% rafinad). **Переписанный** пересказ (синонимы/другой порядок/другие цифры) → другой хэш → проходит как уникальный. Отсюда повторы.
  - **В коде УЖЕ есть спящий near-dup примитив SimHash** (`modules/deduplication/fingerprints.py`: `create_text_simhash` + `simhash_hamming_distance`, blake2b-шинглы, 64 бита, Хэмминг) — чистый Python, ноль зависимостей/RAM-риска, покрыт тестами, но **НЕ подключён** в `detector.py`. Готовый «бюджетный» путь под текущее железо (отвергнут владельцем в пользу ожидания настоящих embeddings).
  - **Когда вернёмся** (после апгрейда VPS): варианты по возрастанию качества — (1) активировать SimHash-схлопывание в seam `cascaded_bulletin` (intra-batch, порог Хэмминга эмпирически + тесты на «похоже-но-разное»); (2) e5-small/LaBSE через onnxruntime (легче torch); (3) multilingual-e5/LaBSE через sentence-transformers; (4) GigaChat freemium embeddings (1 млн ток/год, нужен RU-номер) за тем же seam. Seam: `filter_posts_list(..., recent_text_fingerprints=[])` / список `cascade_news_posts` в `modules/cascaded_bulletin.py`.
- ~~**Мигрировать `web/api/publisher.py` на extended VKPublisher.**~~ Уже мигрировано (обнаружено 2026-06-03 при аудите хвостов): `web/api/publisher.py` импортирует `from modules.publisher.vk_publisher_extended import VKPublisher`, а все нужные методы (`get_group_info`, `get_target_group_id`, `publish_aggregated_post`, `publish_bulletin`) присутствуют в extended. Старого `vk_publisher.py` в основном дереве нет. Запись была устаревшей.

- ~~**🌍 Модуль авто-регистрации регионов и сообществ (big idea).**~~ MVP закрыт 2026-05-22 (см. ⏳ выше — осталась weekly recheck). Описание ниже сохранено для контекста второй итерации.
  Сейчас новый район добавляется вручную: сам регион в `regions` (код, имя, neighbors, vk_group_id главной ИНФО-группы), потом руками искать все паблики района ВКонтакте и добавлять в `communities` с тематикой (`admin`, `novost`, `reklama`, `sosed`, `kultura`, `sport`, `detsad`, …). Долго, при 12 действующих регионах терпимо, но ручной труд масштабируется плохо.

  **Что должен уметь модуль:**
  1. **Wizard добавления нового региона** — UI-страница `/regions/new`:
     - Поля: код, название, центр-город (для VK geo-search), neighbors (multi-select из существующих регионов), VK-группа главной ИНФО-страницы (опционально, можно создать позже).
     - На submit — `INSERT INTO regions` + сразу запускает discovery-таску.
  2. **VK discovery таска** (Celery): для региона ищет сообщества через комбинацию:
     - `groups.search(q="<город>", count=1000, country=1, city=<vk_city_id>)` — geo-search.
     - `groups.search(q="<город> новости")`, `q="<город> объявления"`, `q="<район> ДТП"` и т.д. — keyword search по списку тем.
     - Чтение VK-стены `wall.get(owner_id=<главная_группа>)` за последний месяц — какие группы репостит сама ИНФО-страница (это уже валидные «партнёры»).
     - Дедупликация по `vk_id`, исключение уже-добавленных.
     Сохраняет результаты в новую таблицу `community_candidates` со статусом `pending`.
  3. **AI-подсказка тематики** — для каждого кандидата Groq получает: название, screen_name, описание (`groups.getById(fields=description,members_count,activity,status`)) + 5 последних постов (`wall.get(count=5)`). Возвращает predicted category + confidence (0-100) + reasoning в 1 фразу. Аналогично — short flag «эта группа выглядит как ИНФО-страница» (нужно поставить как `vk_group_id` региона).
  4. **UI «Найдено N кандидатов»** — на странице `/regions/<code>/discovery`:
     - Каждый кандидат: аватарка, ссылка на VK, AI-категория с возможностью переопределить через dropdown, превью первого поста, кнопки «Добавить» / «Отклонить» / «Отложить».
     - Bulk-операции: «Добавить все с confidence > 70» / «Отклонить все рекламные» / «Только админ-группы».
  5. **Периодическая перепроверка существующих регионов** — Celery beat `weekly`:
     - Для каждого региона повторяет discovery, новые-неизвестные кандидаты → в `community_candidates`.
     - Для каждого уже-добавленного `Community.is_active=True`: пуллит `wall.get(count=1)`. Нет постов > N дней (60? настройка региона) → флаг `health_status='dormant'`. AI-проверка последних 10 постов — если категория сильно сместилась → `health_status='changed_category', suggested_category=...`. Группа удалена/заблокирована (VK error 15/100/203) → `health_status='dead'`.
     - Алёрт в Telegram: «по региону `mi`: 3 новых кандидата, 1 мёртвая группа, 2 сменили тематику».
  6. **Удаление/архивация**: модератор одним кликом помечает мёртвую группу `is_active=False` (НЕ удаляет — историю постов нужно сохранить). UI кнопка «Объединить с другим регионом» для случая «группа уехала из района».

  **Что нужно подсобрать перед стартом:**
  - Миграция: `community_candidates (id, region_id, vk_id, name, screen_name, ai_category, ai_confidence, ai_reasoning, status: pending|approved|rejected|deferred, created_at)`, + `communities.health_status (active|dormant|changed_category|dead)` + `communities.last_post_at` + `communities.checked_at`.
  - VK `groups.search` лимит — ~1000/сутки на токен, надо учесть в global rate-limit.
  - VK `country=1` (Россия) + `city=<vk_city_id>`. Список cities VK не возвращает дёшево, придётся захардкодить mapping регион→vk_city_id в `region_configs` (или ввести админ-поле в UI).
  - Groq: 5 постов × N кандидатов × cost — посчитать quota перед автозапуском.

  **Чек-лист для подзадач:**
  - [ ] Миграция 010 — `community_candidates` + `communities.health_*`.
  - [ ] `modules/discovery/vk_groups_search.py` — обёртка над `groups.search` с гео+ключевиками.
  - [ ] `modules/discovery/ai_categorizer.py` — Groq-prompt для категории + ИНФО-флага.
  - [ ] `tasks/discovery_tasks.py` — `run_discovery_for_region(region_id)` + `recheck_existing_communities()` + beat `weekly`.
  - [ ] `web/api/discovery.py` — CRUD кандидатов + bulk-операции.
  - [ ] `web/templates/region_discovery.html` + `region_new.html` + JS.
  - [ ] Тесты: vk groups.search mock, ai_categorizer happy/fallback, candidate CRUD, health-check логика (`dormant`/`dead`/`changed_category`).

- ~~**Per-region keyword overrides для discovery**~~ Уже реализовано (обнаружено 2026-06-03 при аудите): `modules/discovery/vk_search.py` читает `region.config['discovery_keywords']` как основной источник (CATEGORY_KEYWORDS — fallback), `tasks/discovery_tasks._read_region_discovery_config` парсит и прокидывает. Запись была устаревшей.
- **Discovery — расширенные источники кандидатов для существующих регионов.** Сейчас при ручном re-discovery (кнопка «🔍 Найти новые сообщества») и beat-таске `discovery-rolling-daily` используются те же source'ы что и при создании региона: `groups.search` по localities + keywords + info-repost ([PR #51](https://github.com/Valstan/setka/pull/51)). Идеи новых источников: **(a)** подписки/`groups.get` админов уже-добавленных сообществ; **(b)** `members.get` главной ИНФО-страницы → `users.getSubscriptions` top-N активных; **(c)** `wall.search`/`newsfeed.search` по localities; **(d)** парсинг hashtag'ов и `@-mentions` из постов главной. **⏳ Отработано в скиле `discover_scan.py` на Малмыже (май 2026), с эмпирикой:** локалити-автозапросы — главный выигрыш (×2.7 кандидатов); блок «Ссылки» главной (`groups.getById fields=links`) — высокоточный, добавлен 5-м источником; `(c)` через `newsfeed.search` мощен в холодном бурсте, но VK **мягко троттлит до пустого** (`count:0` без ошибки) — гонять редко; `(d)` упоминания дают мало, но хэштеги питают newsfeed; **`(a)/(b)` НЕ работают обычным токеном** — `groups.getMembers(managers)` → VK error 15 «you should be a group administrator» (нужен админ-токен). ⏳ **Частично перенесено в `vk_search.py` (beat/UI-путь) 2026-05-31 ([PR #97](https://github.com/Valstan/setka/pull/97)):** блок «Ссылки» главной (`info_links`, `VKClient.get_groups_by_refs` + `_harvest_main_group_link_refs`) — Step 0b, после репостов. Локалити-автозапросы там уже были (Step 2). **Намеренно НЕ перенесены:** `(c) newsfeed.search` (мягкий троттл до пустого — для частого beat-пути вреден), `crawl-subscriptions` (`(a)/(b)`, нужен админ-токен), post-text упоминания `(d)` (дают мало). Остаток пункта закрыт по существу.
- 🟢 **Locality-скоринг discovery — омонимы коротких/общих стемов.** Наивный стем `_make_stem` ловит ложные: `Калинино`→`калинин` матчит фамилию «Калинина»; `Старый/Новый/Большой` тянут чужие «Старый Оскол», «Петергоф» и т.п. (так же как F-баг тужи). Идея: словарь стоп-стемов общеупотребимых слов + вес distinctive-стемов > generic. Сейчас отсекается нейро-классификацией на Шаге 4 скила.
- 🟢 **Длинный хвост сельских пабликов района — только ручным знанием.** Эмпирика Малмыжа: даже после 5 источников recall ≈45% от ручного пула (79 групп); крошечные сельские СДК/библиотеки (<100 подписчиков) VK-поиск не индексирует и нигде не слинкованы. Засев района — гибрид: скил даёт основу, длинный хвост добирается локальным знанием.
- **Discovery — фоновый «watcher» репостов главной ИНФО-страницы.** Расширение PR #51: вместо ad-hoc вычитки при manual trigger, beat-таска раз в N часов сканирует последние посты главной ИНФО-страницы каждого активного региона, извлекает `copy_history.owner_id`, добавляет неизвестные группы в `community_candidates(source='info-repost-watch', status='pending')`. Эффект: за неделю автоматически набираются «дружественные» группы которые уже репостят друг друга. Дёшево по VK-квоте. **⏸ Отложено 2026-06-03:** любая авто-discovery beat-таска идёт против текущего курса (`discovery-rolling-daily` намеренно отключён в PR #108 — «вручную через `/discover_communities`, пока нет нейро-фильтра»). info-repost высокоточен (кандидаты в `pending` на ручной обзор), но включать не раньше решения владельца / нейро-классификации.
- **Quota guard для Groq в discovery** — посчитать сколько токенов уходит на discovery 100 кандидатов (~prompt 500t + response 100t = 60K tokens per region). Если станет дорого — кешировать ai-результаты per (vk_id, hash(description)).
- **`discovery-rediscover-monthly` beat-таска** — поверх weekly recheck'а добавить ежемесячный re-`run_discovery_for_region` по всем `Region.is_active=True`. Сейчас можно дёргать только ad-hoc из UI или Celery shell. Очевидный риск — Groq quota и VK groups.search limit (~1000/сутки на токен) при 12+ регионах. Альтернатива: разнести по дням недели (mi в понедельник, vp во вторник …) — `crontab(day_of_week=…)` per-region. **⏸ Отложено 2026-06-03:** это буквально авто-`run_discovery_for_region` (groups.search), который отключён в PR #108 (~98% мусора без нейро-фильтра). Реализация = откат решения. Не делать без нейро-классификации.
- ~~**UI «changed_category» quick-action**~~ Закрыто 2026-06-01 (этот PR): на `/communities` добавлен фильтр «Здоровье» (`health_status`, в т.ч. `changed_category`), бейдж «AI: <тема>» в ячейке категории и кнопка-«магия» — endpoint `POST /api/communities/{id}/apply-suggested-category` переносит `suggested_category` → `category`, сбрасывает `health_status='active'` и очищает подсказку одним кликом. +6 тестов. Backend `CommunityResponse` теперь отдаёт `health_status`/`suggested_category`; сериализация выведена в `_community_to_dict` (DRY 4 хендлеров).
- ~~**UI «История публикаций» по регионам и темам**~~ Закрыто 2026-06-03 ([PR #125](https://github.com/Valstan/setka/pull/125)): страница `/publications` + `GET /api/parsing-stats/publications` (фильтры region/theme/days). Переиспользует `parsing_stats` (новой таблицы не заводили) — `_save_stats` теперь пишет `published_url`/`published_post_id`. Таблица: дата, регион, тема, постов, ссылка на VK-пост.
- ~~**«Тёмный режим» для UI**~~ Закрыто 2026-06-03 (ветка `feat/dark-mode`): переключатель темы (луна/солнце) в навбаре через нативный Bootstrap 5.3 `data-bs-theme`, выбор в `localStorage` (`setka-theme`), inline-init в `<head>` без мигания. Хардкод-цвета в `style.css` (body/card-header/table/scrollbar) переведены на тема-зависимые `var(--bs-*)`. Применяется ко всем страницам через `base.html`. _Браузер-верификация — за владельцем (агент не открывает UI)._
- ~~**`/regions/<code>/diagnostics`** — кнопка «прогнать пайплайн без публикации»~~ Закрыто 2026-06-03 ([PR #124](https://github.com/Valstan/setka/pull/124) + seam [PR #122](https://github.com/Valstan/setka/pull/122)): `parse_and_publish_theme(dry_run=True)` (+ каскад) — truly-dry прогон (парс/фильтр/сборка без публикации и без записи в БД), возвращает `would_publish`. Страница `/regions/<code>/diagnostics` ставит задачу в Celery и опрашивает по task_id; показывает счётчики фильтрации + превью сводки. _Браузер-верификация за владельцем._
- **Полноценный Telegram-бот с webhook** — `bot.set_webhook` + `wall.createComment`/`messages.send` прямо из bot-handler без перехода в браузер. Сейчас (этап 4b) — URL-кнопки на `/notifications#section=...`, требуют один лишний клик. Это «фича роскоши», не блокер.
- ~~**Per-region шаблоны ответов**~~ Закрыто 2026-06-03 (ветка `feat/per-region-templates`): миграция 024 (`message_templates.region_id` NULL=общий, FK `ON DELETE SET NULL`). `/templates` — колонка «Регион», select в модалке («Общий» / регион), фильтр по региону. `GET /api/templates/?region_id=X` отдаёт общие + специфичные для X (для dropdown ответа). +5 тестов.

---

## История пересечений

Если задача висела долго и пересекалась с несколькими сессиями — пиши тут историю переноса дат, чтобы было видно, что она «застряла».

_Сейчас пусто._
