# 🔐 Радар-ID — OIDC-провайдер идентичности экосистемы (решение владельца через brain 2026-06-30)

> Запись `P057` реестра [PENDING_FOLLOWUPS](../PENDING_FOLLOWUPS.md). Индекс грепается, запись читается целиком, реестр целиком не читается никогда ([D-097](../../AGENTS.md)).

`⏱ 2026-06-30 · snooze 0 · ✅ РАСКОНСЕРВИРОВАНО 2026-08-28 по ре-триажу — условие «trener пошёл на round-trip» наступило 2026-07-31` _(ре-триаж 2026-08-28: `parked` снят. Прод, `oauth_refresh_tokens` по client_id=trener — 8 строк начиная с 2026-07-31 08:51 и по 2026-08-22 09:15, ни одна не revoked; `oauth_auth_codes` по trener — 12. Живого инженерного остатка в Ф1 больше нет, пункты (1) и (2) закрыты ниже; открытыми остались только (3) зеркало ключа в Карман и (4) нит по nginx)_ _(ре-триаж 2026-08-09: 40 дней; `watch` держался на чужом ходе, а по правилу шапки это `parked` с явным условием — иначе пункт всплывает каждую сессию, ничего не сообщая. Ф1 живёт на проде с 07-05, наша сторона закрыта)_ _(ре-триаж 2026-07-27: 27 дней → watch; ждёт хода соседнего проекта)_

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
- ✅ **AuthGate `/oidc/authorize` 401→302 для не-браузерных клиентов (trener пункт #1)** — закрыто 2026-07-11
  ([PR #332](https://github.com/Valstan/setka/pull/332), задеплоено): `FRONT_CHANNEL_GET_PATHS` в
  `middleware/auth_gate.py` — неаутентифицированный GET на authorize-эндпоинт всегда 302 на login (front-channel,
  спекосообразнее), даже без браузерного `Accept`; curl-смоук без `-A Mozilla` больше не даёт ложный 401. Ответ
  brain — `mailbox/to-brain/2026-07-11-authgate-oidc-authorize-302-fixed.md`.
- ✅ **ЕДИНЫЙ ВХОД ЭКОСИСТЕМЫ — сторона setka ЗАКРЫТА (заказ владельца 2026-07-19, PR #367/#368,
  задеплоено):** (а) **радар.вмалмыже.рф** — публичный домен ленты Радара (Джино: A-запись +
  поддомен→VPS СЕТКА + LE-серт на edge; на боксе `/etc/nginx/conf.d/radar_feed.conf`, вне git,
  корень 302→`/radar`, `absolute_redirect off`); (б) сессионная кука расшарена на все поддомены
  (`SESSION_COOKIE_DOMAIN=.xn--80adkdyec4j.xn--p1ai` в прод-env; дефолт пустой = host-only) —
  вход единый между вход./радар.; (в) **брендированная страница входа**: `/login` распознаёт
  источник (OIDC `next=/oidc/authorize?client_id=X` → `oauth_clients.branding` JSON, миграция
  072; поддомен СЕТКИ → свой бренд; иначе «🌿 Сервисы Малмыжа») и рисует «Войти в <Сервис>» со
  значком/цветом/слоганом + кнопка «Войти через ВКонтакте»; (г) **фикс round-trip**: radar-роль
  теперь уважает `next=/oidc/...` (раньше форсила `/radar` — сломало бы возврат внешним клиентам).
  Брендинг trener записан в прод-БД (⚽ ТРЕНЕР). _Применение: миграция 072 + restart web — сделано._
- ⏸ **Единый вход — ждём ТОЛЬКО ГОНЬБУ (письмо мозгу
  `mailbox/to-brain/2026-07-19-unified-login-for-all-services.md`, `recommend`):** ~~Сабантуй и~~
  Гоньба должн~~ы~~а подключиться OIDC-клиентом (кнопка «Войти» → redirect на вход.вмалмыже.рф), как
  trener. Нужно: точный redirect_uri (punycode) + брендинг (название/значок/цвет/слоган).
  Дальше я регистрирую клиента (`scripts/register_oidc_client.py`), кладу branding в БД, отдаю
  секрет через root-файл. Кука-шеринг чужим VPS **не** даём (секрет подписи = вся экосистема).
  **~~Сабантуй~~ — ПОДКЛЮЧЁН 2026-07-28, половина записи протухла на месяц.** Прод,
  `SELECT client_id, name, created_at, branding IS NOT NULL, is_active FROM oauth_clients`:
  trener (2026-07-05), **sabantuy «Сабантуй в Малмыже» (2026-07-28 13:28, redirect_uri
  `https://xn--80aac7atuli.xn--80adkdyec4j.xn--p1ai/api/auth/esa/callback`, брендинг есть,
  активен)**, portal (2026-07-28 21:45), karman (2026-08-25). Гоньбы среди клиентов нет.
  `⏱ 2026-07-19 · snooze 0 · parked — ход соседа: условие снятия «проект Гоньба прислал
  redirect_uri + брендинг»`
- ✅ **`/oidc/token` 500 на боевом обмене — ПОЧИНЕНО** (письмо brain 2026-07-26 `radar-token-500-blocks-live-round-trip`,
  urgency high; этот PR): первый живой round-trip клиента `trener` (26.07 15:44:49 / 15:45:38 MSK) порвался на
  обмене кода. В прод-логе — `DatatypeMismatchError: column "family_id" is of type uuid but expression is of
  type character varying`: миграция 052 создала `oauth_refresh_tokens.family_id UUID`, модель объявляла
  `String(36)`. **Тот же класс, что `radar_users.sub` в PR #381** — вторая колонка той же схемы, первый фикс её
  пропустил. Диагностика клиента была верной: 401-ветка (неверный секрет) отвечала штатно, падало **дальше по
  коду**, на записи refresh-токена. Правка — моделью (`PgUUID(as_uuid=False)`), схему и данные не трогаем.
  Чтобы третьего раза не было, вместо точечного теста поставлен **выведенный гейт**
  `tests/test_schema_type_parity.py`: парсит все миграции и требует совпадения «UUID / не UUID» с ORM в обе
  стороны (проверен негативно — ловит оба инцидента, 25.07 и 26.07). +5 тестов, 1797 зелёных.
- 🟢 **Остаток Ф1:** ~~(1) round-trip-smoke с trener (#011) — владелец передаёт trener client_secret из
  root-файла + issuer; когда trener построит свою сторону → пинг brain, подключит GONBA/Sabantuy.~~
  **✅ ЗАКРЫТО 2026-08-28 по ре-триажу — round-trip состоялся 2026-07-31 и повторялся ещё семь раз.**
  Прод: `SELECT client_id, created_at FROM oauth_refresh_tokens ORDER BY created_at` → trener
  2026-07-31 08:51, 08-03 06:26, 08-03 10:14, 08-04 07:44, 08-04 12:46, 08-04 12:47, 08-11 08:54,
  08-22 09:15 — 8 строк, ни одна не revoked; `oauth_auth_codes` по trener — 12. Строка refresh-токена
  пишется ровно на том шаге, который падал 500 (`family_id` uuid vs `String(36)`), значит обмен
  прошёл успешно восемь раз уже после фикса.
  ~~(2) владельцу — физически проверить `https://вход.вмалмыже.рф/auth/vk/login` (вход через ВК → /radar).~~
  **✅ ЗАКРЫТО 2026-08-28 по ре-триажу — дубль уже закрытой секции** «~~🔴 Вход через ВКонтакте
  сломан во всей экосистеме~~ ЗАКРЫТО 2026-07-26 — ложная тревога» (выше в «🔴 Блокеры»): там
  записано «Проверено вживую в браузере: `/auth/vk/login` → диалог VK „Выбрать аккаунт“, владелец
  успешно вошёл в Радар». Подтверждено данными: прод `SELECT id, login, vk_user_id IS NOT NULL,
  created_at FROM radar_users` → id=2, `login` пуст, `vk_user_id` заполнен, created_at
  2026-07-25 20:23 — соц-only строка создаётся только успешным VK-callback.
  (3) RS256-ключ подписи — кандидат №1 на зеркало в Карман (ADR-0006), когда KARMAN даст mirror-API.
  (4) нит: nginx-блок Радара — ACME-location через webroot оставлен, но реально TLS на edge Джино;
  можно упростить при следующем касании (не блокер).
- 🟢 **Ф2/Ф3 (не сейчас):** magic-link (R12) + Telegram-HMAC + клиент №2 (Ф2); остальные клиенты +
  мобайл-PKCE футбол/такси (Ф3). Когда Сабантуй перейдёт на вход через Радар-ID — его отдельное
  VK ID-приложение (App ID 54656174) выводится, остаётся одно на экосистему (Вариант А, анти-зоопарк).
