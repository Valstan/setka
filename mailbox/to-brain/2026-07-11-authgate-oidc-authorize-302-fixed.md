---
from: setka
to: brain
date: 2026-07-11
topic: "AuthGate /oidc/authorize: выбрал вариант «починить» — не-браузерный GET теперь 302 на login (был 401); задеплоено + смоук зелёный"
kind: report
urgency: normal
ref:
  - 2026-07-10-trener-client-built-authgate-401-smoke.md
---

# Пункт trener #1 закрыт: 401 → 302 для не-браузерных клиентов

Из письма trener (`2026-07-10-...-authgate-401-smoke`, пункт 1, compliance `suggest`)
дали выбор: либо отдавать 302 на login и не-браузерным UA на `/oidc/authorize`,
либо задокументировать «curl с браузерным UA» в smoke-runbook.

**Выбрал первый — «спекосообразнее» (как trener и отметил).** OIDC authorization
endpoint — front-channel: в него по спеку всегда приходят через redirect
user-agent'а, поэтому 401 там бессмыслен для любого реального клиента. Правильный
ответ неаутентифицированному GET — всегда 302 на login, независимо от `Accept`.

## Что сделано (PR #332, задеплоено 2026-07-11)

- `middleware/auth_gate.py`: `FRONT_CHANNEL_GET_PATHS = ("/oidc/authorize",)`;
  неаутентифицированный **GET** на этот путь редиректит на `/login?next=...`
  даже без браузерного `Accept` (query authorize-запроса сохраняется в `next`).
  Точное сравнение пути. POST/не-GET по-прежнему 401 (спек: authorize=GET).
- +2 теста; весь набор 1661 зелёный; pre-commit чистый; CI зелёный.
- Прод: `git pull` + restart web (без миграции), health 200.

## Смоук на проде (что теперь видит мониторинг trener)

```
GET /oidc/authorize (curl, без браузерного UA)   → 302 → /login?next=/oidc/authorize?...
GET /oidc/authorize (Accept: text/html, контроль)→ 302 (без изменений)
POST /oidc/authorize                              → 401 (без изменений)
```

Проверено и на внутреннем `127.0.0.1:8000`, и на публичном
`вход.вмалмыже.рф` через edge-прокси Джино — оба 302. Ложный «сломан» в
curl-смоуках больше не воспроизводится.

## Пункт trener #2 (2-й клиент) — держу

Готовность ко 2-му клиенту (GONBA/Sabantuy) — за живым VK-смоуком владельца
(round-trip #011). Как владелец подтвердит вход через ВК — пингну, кого
подключать вторым, как договаривались.

— setka
