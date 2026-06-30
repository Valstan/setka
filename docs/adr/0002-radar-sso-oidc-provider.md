# ADR-0002: «Радар-ID» — Радар становится OIDC-провайдером идентичности всей экосистемы

**Date:** 2026-06-30
**Status:** Proposed (design-first; ответы владельца получены 2026-06-30 — см. §«Решения владельца»; постройка Ф1 после ратификации контракта brain)
**Drives:**
- [brain mailbox 2026-06-30-radar-as-ecosystem-sso-center](../../mailbox/to-brain/2026-06-30-radar-sso-contract.md)
- [brain_matrica/docs/plans/unified-auth-concept.md](../../../brain_matrica/docs/plans/unified-auth-concept.md) (§«Пересмотр решения 2026-06-30», Часть A — канон)
- REFERENCE [R16](../../../brain_matrica/cross-project-ideas/REFERENCE.md) (VK ID OAuth 2.1 PKCE), [R12](../../../brain_matrica/cross-project-ideas/REFERENCE.md) (Payload magic-link), ADR-0006 brain (зеркало секретов в Карман)

## Контекст

Владелец 2026-06-30 принял стратегическое решение: **единый центр идентичности всей публичной
экосистемы @valstan** (GONBA, SabantuyMalmyzh, малмыж-сайты ×3, trener, будущие футбол/такси) =
**модуль «Радар» внутри Сарафан/setka**. Это пересмотр прежнего решения 06-13 (был отдельный
мини-проект `passport` на боксе GONBA) — `passport`-репо/DNS **не заводятся**.

trener — клиент №1 по времени (ждёт OIDC-контракт). Запрос пришёл тремя письмами brain 2026-06-30
(`radar-as-ecosystem-sso-center`, `radar-auth-vk-arch-unified-login`, `radar-sso-contract-from-trener`).

**Канон (Часть A unified-auth — держится при любом доме):** OIDC Authorization Code + PKCE
(redirect-flow — разные домены не делят cookie); authn↔authz раздельно (Радар = «кто ты», роли
каждый сайт держит локально); 152-ФЗ — один store аккаунтов, одна точка «права на забвение», одно
согласие; соц-логины (VK/Яндекс/TG) — upstream-методы входа Радара, не «система».

**Что в setka уже есть (заземление):** FastAPI 0.118 + Starlette; stateless signed-cookie auth
(`modules/radar/auth.py`); модель `RadarUser` (login/password_hash scrypt/role operator|radar/quota);
инвайт-код регистрация; AuthGateMiddleware. Соц-привязка сегодня — **бот-паттерн** (`account_link.py`
Telegram, `vk_intake.py` VK community-DM) — это НЕ redirect-OAuth, для SSO нужен отдельный VK ID
OAuth-flow (R16).

## Решение

### 1. Радар-ID — multi-client OIDC-провайдер на Authlib

Реализуем **OIDC Identity Provider** как модуль setka. Крипту/протокол — **проверенной библиотекой
Authlib** (де-факто OIDC-провайдер для Python: AuthorizationServer + AuthorizationCodeGrant +
OpenIDCode + PKCE + JOSE/JWKS), **не руками** (канон). Своего пишем только UI логина/согласия и
upstream-коннекторы.

**Публичные эндпоинты** (issuer `https://вход.вмалмыже.рф` — решение владельца 2026-06-30, A-запись на хост Радара/setka; **punycode** `xn--b1ae3a1a.xn--80adkdyec4j.xn--p1ai` для регистрации в ВК-приложении и redirect_uri — G108/R16):
- `GET /.well-known/openid-configuration` — discovery.
- `GET /.well-known/jwks.json` — публичные ключи (RS256), для **офлайн-валидации клиентами**.
- `GET /authorize` — Authorization Code + PKCE; если не авторизован → 4-методная login-страница; затем consent → выдаёт `code`.
- `POST /token` — `code`+`code_verifier` → `id_token`(RS256)+`access`+`refresh`; refresh-grant с ротацией.
- `GET /userinfo` — claims по access-токену.
- Upstream-callbacks: `/auth/vk/callback`, `/auth/telegram/callback`, `/auth/yandex/callback`, `/auth/email/verify`.

> **Коллизия имён разведена (open-вопрос brain):** зонтик **«Радар» = платформа Сарафан**; её модули —
> **«Радар-ID»** (аккаунты/SSO, этот ADR) и **«Радар-лента»** (контент-агрегатор, существующий). Один
> аккаунт-слой обслуживает и личные ленты, и SSO для сайтов. *Подтверждение нейминга — за владельцем.*

### 2. Модель данных (расширение `RadarUser` = аккаунт-слой)

`RadarUser` становится каноническим аккаунтом (brain: контент-Радар = публичный продукт с аккаунтами).
Миграция добавляет:
- `sub` (UUID, unique, **opaque** — стабильный OIDC subject; НЕ отдаём serial PK наружу: утечёт счётчик пользователей);
- `email` (citext, unique nullable), `email_verified` (bool) — флаг критичен: по нему решают, можно ли связать соц-личность с существующим аккаунтом без захвата;
- `display_name`;
- `vk_user_id` / `telegram_user_id` / `yandex_id` (unique nullable) — связанные upstream-id;
- `password_hash` → **nullable** (соц-only аккаунт без пароля); `login` → nullable (email становится осн. идентификатором).

Новые таблицы: `oauth_clients` (client_id, client_secret_hash, redirect_uris allowlist, allowed_scopes,
name, is_confidential), `oauth_auth_codes` (code, client, user, redirect_uri, code_challenge, scope,
nonce, expires — single-use), `oauth_refresh_tokens` (token_hash, **family_id** для reuse-detect,
user, client, rotated_from, revoked_at).

**152-ФЗ:** чтение коллекции пользователей — admin/сам, **не anyone** (это auth, утечка email/соц-id
критична — pool #016/G9); claims-минимизация; удаление `sub` → каскад (одна точка «права на забвение»).

### 3. Claims и scopes (разный набор данных по сайтам — без N приложений)

Радар собирает **суперсет один раз** (с согласия), каждый клиент запрашивает **только нужные scopes**:
- `openid` → `sub`; `profile` → `name`; `email` → `email` + `email_verified`.
- Per-client allowed-scopes в `oauth_clients` → клиент физически не получит больше, чем разрешено.

Это и есть ответ на «где-то имя, где-то +email/др.» — слой claims, не отдельные приложения.

### 4. Архитектура VK-приложений: **одно приложение на слое Радара** (рекомендация, brain-ask #1)

| | Вариант А: одно общее ВК-приложение | Вариант Б: ВК-приложение на каждый сайт |
|---|---|---|
| Кто ходит в VK | **только Радар** (сайты получают identity редиректом) | каждый сайт сам |
| redirect_uri в VK-приложении | **один** (`id.малмыже.рф/auth/vk/callback`) на всю экосистему | N (по сайту), каждый ведёт свой allowlist |
| Верификация приложения ВК | **однократно** для Радара | N× (на каждое приложение) |
| «Разный набор данных» | слой claims (scopes) | ложно требует N приложений |
| «Падение одного не роняет» | покрыто офлайн-JWKS (см. §5), не дроблением ВК | дробление ВК ≠ resilience |
| Обслуживание | один кабинет/ключ/точка ротации (→ Карман, ADR-0006) | N кабинетов |

**Рекомендация: Вариант А, уточнённый — VK = upstream-метод самого Радара, одно ВК-приложение.**
Сайты **никогда не ходят в ВК напрямую**. Дилемма А/Б на уровне сайтов исчезает (у сайта нет своего
ВК-приложения). Лимиты ВК не мешают: login-трафик низкий (люди логинятся), одного приложения хватает;
один redirect_uri проще верифицировать. *Оговорка:* community-management ВК (постинг от имени паблика
сайта) — **отдельная** забота (community-токены, у setka уже есть), НЕ user-login; не путать.

Техника ВК-входа — **REFERENCE R16** адаптированный под Python (`authlib` вместо ручного): `id.vk.ru`
(не старый oauth.vk.com), Authorization Code + PKCE S256, **`device_id` из callback обязателен**,
`redirect_uri` символ-в-символ + **punycode для `.рф`** (G108).

### 5. MUST-митигаторы (SSO теперь в failure-domain setka — из «желательно» в «обязательно»)

1. **Офлайн-валидация JWKS клиентами** — главный митигатор сцепления: сайты кэшируют публичные ключи,
   валидируют `id_token` локально → падение setka блокирует только **новые логины**, просмотр контента жив.
2. **RS256**, приватный ключ ротируемый, в секретах (#008) + **зеркало в Карман** (ADR-0006 brain —
   потеря ключа подписи = пере-выпуск для всей экосистемы). Короткие access (5–15 мин) + **ротация
   refresh с детектом повторного использования** (reuse старого refresh → отзыв всей family).
3. **Rate-limit** на `/authorize`, `/token`, соц-callbacks; **audit-лог** логинов/выдачи токенов;
   `state`+PKCE+`nonce` анти-CSRF; **Telegram Login — проверка HMAC** ботом на стороне Радара.
4. **Наблюдаемость SSO-эндпоинтов** (#018) — общий вход «молча встал» дороже под автономией; health/heartbeat публичной OIDC-поверхности.

### 6. authn↔authz граница (переносимый принцип всех клиентов)

Радар отдаёт **только identity** (`sub`/`email`/`email_verified`/`name`). «Что можно» — каждый клиент
решает локально своими ролями. trener подметил: VK даёт «кто человек», но привязку `родитель→ребёнок`
trener держит у себя (доменная авторизация, 152-ФЗ). **SSO решает аутентификацию, не доменную привязку.**

### 7. 4-методная login-страница (живёт в Радаре, brain-ask #3)

Каноническая login-страница (authorize провайдера) — **в Радаре**. Клиентские сайты ставят тонкую кнопку
«Войти» → **redirect в Радар** (не своя модалка на каждом сайте — разные домены не делят cookie, redirect
единственно корректен). Модалка с 4 методами — одна, в Радаре:
1. **Логин+пароль** — локальный `RadarUser` (scrypt уже есть).
2. **Email magic-link** — **R12-паттерн** в Python: подписанный single-use токен, в БД только `sha256`,
   короткий TTL, **two-step GET-validate / POST-consume** (пережить email-префетч/SafeLinks — G96). SMTP уже обкатан у Sabantuy/trener.
3. **ВК** — R16 (см. §4).
4. **Telegram** — Telegram Login Widget, проверка **HMAC** подписи ботом на стороне Радара.

Все 4 резолвятся в `RadarUser` (связывание по verified-email / соц-id) → Радар выдаёт OIDC-`code` клиенту.

### 8. Регистрация клиентов

Ручная (не dynamic-registration): оператор заводит клиента (GONBA/Sabantuy/trener/…) через
операторский UI/CLI → генерит `client_id` + `client_secret` (confidential server-side) либо PKCE-only
(публичный/мобайл футбол/такси), пишет redirect_uri-allowlist + allowed-scopes.

## Фазы (постройка — после ратификации контракта и ответов владельца)

- **Ф0 (этот ADR):** дизайн + контракт brain. Постройки нет.
- **Ф1:** миграция (расширить `RadarUser` + 3 oauth-таблицы) + Authlib OIDC-ядро
  (discovery/jwks/authorize/token/userinfo) + локальный логин + **один upstream (ВК R16)** + регистрация
  клиента №1. **Smoke — полный round-trip логина** (#011) на одном клиенте (GONBA или trener).
- **Ф2:** magic-link (R12) + Telegram-HMAC; клиент №2 (проверка cross-domain redirect на двух доменах).
- **Ф3:** остальные клиенты; мобайл-PKCE для футбол/такси.
- **Перед деплоем Ф1:** публичный домен + TLS на хосте Радара (новая экспозиция setka — owner-решение).

## Решения владельца (2026-06-30) — открытые вопросы закрыты

1. **Нейминг — ✅ подтверждён:** зонтик «Радар = платформа Сарафан», модули **Радар-ID** (SSO) /
   **Радар-лента** (контент).
2. **Публичная поверхность — ✅ `вход.вмалмыже.рф`** (issuer; A-запись → хост Радара/setka + TLS).
   Кириллический IDN → **punycode** `xn--b1ae3a1a.xn--80adkdyec4j.xn--p1ai` обязателен в ВК-приложении
   и в `redirect_uri` символ-в-символ (G108/R16). TLS на хосте setka — новая публичная экспозиция,
   решить при деплое Ф1.
3. **Пилотный клиент Ф1 — ✅ trener** (клиент №1 по времени; round-trip-smoke на нём).

Остаётся **ратификация контракта brain** (может уточнить протокол/claims) → затем постройка Ф1.

## Последствия

**Плюсы:** один вход на всю экосистему; 152-ФЗ-минимизация (один store/согласие/«забвение»); credential-locality VK (шлюз #062) — в плюс; стандартный OIDC → дешёвые кросс-языковые клиенты.

**Минусы / принятые trade-offs (осознанный выбор владельца, не дрейф):** SSO сцеплен с аптаймом setka
(митигатор — офлайн-JWORS §5.1, обязателен); теряется плейбук GONBA (node-oidc-provider) — setka берёт
Authlib; новая публичная HTTPS-поверхность у «внутреннего» setka (нужен домен+TLS).

**Безопасность:** приватный ключ подписи — критичный секрет (#008 + зеркало Карман ADR-0006); коллекция
пользователей — admin/self read (G9); крипта — Authlib, не руками.
