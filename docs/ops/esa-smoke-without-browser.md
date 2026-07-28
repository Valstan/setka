# Смоук-проверка ЕСА без браузера (для проекта-потребителя)

> Ответ на вопрос brain от 2026-07-28: «есть ли у ЕСА смоук-путь без браузера
> владельца — тестовый клиент, dev-режим, фиктивный провайдер?»
>
> **Есть.** Круг OIDC проходится четырьмя `curl` — ни браузер, ни VK-аккаунт не
> нужны. VK — лишь **один из** upstream-методов логина, а не единственный вход.
> Узкое место другое, и оно названо в конце: тестовый аккаунт выдаёт человек.

Issuer — `https://вход.вмалмыже.рф` (punycode `xn--b1ae3a1a.xn--80adkdyec4j.xn--p1ai`;
в OAuth-полях обязателен punycode — [G108]).

## Что нужно один раз

1. **Регистрация клиента** — на стороне SETKA, `scripts/register_oidc_client.py`
   (даёт `client_id` + `client_secret`, показывается один раз).
2. **Тестовый аккаунт** — логин и пароль обычного `RadarUser`. Сейчас его
   **выдаёт владелец**: самореги на проде нет (см. «Узкое место»).

## Круг целиком — четыре curl

```bash
ESA=https://xn--b1ae3a1a.xn--80adkdyec4j.xn--p1ai

# 1. Логин по паролю → сессионная кука. Ни VK, ни браузера.
curl -s -c jar.txt -X POST "$ESA/api/auth/login" \
  -H 'Content-Type: application/json' \
  -d '{"login":"<тестовый логин>","password":"<пароль>"}'
# → {"ok":true,"role":"radar","login":"…"}

# 2. Authorize с этой кукой → 302 на ваш redirect_uri с ?code=…
curl -s -b jar.txt -o /dev/null -w '%{redirect_url}\n' \
  "$ESA/oidc/authorize?client_id=<ваш>&response_type=code\
&redirect_uri=<точный punycode-uri>&scope=openid%20profile&state=smoke"

# 3. Обмен кода на токены (client_secret_basic или client_secret_post)
curl -s -u "<client_id>:<client_secret>" -X POST "$ESA/oidc/token" \
  -d grant_type=authorization_code -d code=<code> \
  -d redirect_uri=<тот же uri>
# → {"access_token":"…","id_token":"…","refresh_token":"…"}

# 4. Claims по access-токену
curl -s -H "Authorization: Bearer <access_token>" "$ESA/oidc/userinfo"
```

Контракт эндпоинтов машинно-читаем: `GET /.well-known/openid-configuration`
(и `/.well-known/jwks.json` для офлайн-валидации `id_token`) — оба публичные,
их можно дёрнуть прямо сейчас, без всякой регистрации.

Consent-экрана в Ф1 нет: клиенты — first-party экосистемы, согласие неявное
([ADR-0002](../adr/0002-radar-sso-oidc-provider.md) §8). Поэтому шаг 2 и
отдаёт редирект сразу, а не HTML-страницу — именно это делает круг headless.

## Узкое место — не браузер, а тестовый аккаунт

Замер на проде 2026-07-28 (`curl` с самого хоста):

| Проверка | Результат | Что означает |
|---|---|---|
| `GET /.well-known/openid-configuration` | `200` | discovery живой |
| `GET /oidc/authorize` без сессии | `302 → /login?next=…` | гейт по **сессии**, не по VK |
| `POST /api/auth/login` (неверные) | `401` | парольный путь есть и работает |
| `POST /api/auth/register` | `403 «Регистрация отключена»` | **самореги нет** |

Саморег выключен ровно одной переменной: `web/api/auth.py` требует
`RADAR_INVITE_CODE`, а её нет в `/etc/setka/setka.env` → любой `register`
отдаёт 403. То есть потребитель **не может завести себе смоук-аккаунт сам** —
и именно это, а не «нужен браузер с живым VK», делает первую проверку дорогой.

**Два способа снять** (оба — решение владельца):

- задать `RADAR_INVITE_CODE` на проде и выдать код проектам — тогда каждый
  заводит смоук-аккаунт сам, полностью headless;
- либо выдавать по одному тестовому аккаунту на потребителя вручную.

Первый дешевле в эксплуатации, второй — точнее по учёту. До этого шага
инструкция выше рабочая, но требует одного человеческого действия на старте.

[G108]: ../../../brain_matrica/cross-project-ideas/GOTCHAS.md#g108
