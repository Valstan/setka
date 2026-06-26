---
from: setka
to: brain
date: 2026-06-26
topic: "Паттерн: один проект как authenticated API-шлюз к привилегированному ресурсу для sibling-проектов (SARAFAN → VK-ворота). Execute-and-return, НЕ выдача credential."
kind: idea
compliance: suggest
urgency: normal
---

# Находка: проект-шлюз к привилегированному ресурсу для остальных проектов

## Боль

У владельца несколько проектов (@valstan). В AI-сессии «другого» проекта типичный
запрос «сходи в VK — проанализируй сообщество / скачай / импортируй» упирается в
закрытость VK 2026 (вход только по логину). При этом у **одного** проекта (SARAFAN)
уже есть весь привилегированный доступ: рабочие VK-токены, клиент, smart-routing с
cooldown, per-token rate-limiter. Дублировать это в каждый проект — дорого и опасно
(N мест с токенами = N точек утечки + N независимых rate-budget'ов, бьющих один
аккаунт).

## Решение (построено и задеплоено за сессию)

SARAFAN стал **read-only HTTP-шлюзом** к VK для остальных проектов: `/api/gateway`
(`POST /call` с allowlist read-методов + `GET /community` + `GET /wall`). Проект
шлёт задачу с `X-API-Key` → SARAFAN исполняет её своим токеном со своего IP под
своим rate-limit → возвращает JSON. + операторская страница статистики
(`/gateway-stats`): кто/когда/сколько + сохранённые параметры запросов.

## Переносимые принципы (применимы к любому ресурсу с привилегированным доступом)

1. **Execute-and-return, НЕ выдача credential — и это не только про безопасность.**
   Многие API (VK точно) **привязывают токен к IP выпуска**: чужой проект с нашим
   токеном со своего сервера получает `error 5 (access_token was given to another ip
   address)`. То есть «выдать токен» **технически не работает** — остаётся только
   «исполни задачу своим credential и верни результат». Бонусом credential живёт в
   одном месте (blast radius не растёт). Это аргумент сильнее, чем обычное «не свети
   секреты»: даже если бы захотел раздать — не получится.

2. **Два независимых слоя rate-защиты.** *На границе credential* — per-token
   rate-limiter + auto-cooldown по кодам ошибок (защита аккаунта от бана). *На границе
   потребителя* — per-API-key квота (защита общего бюджета: один потребитель не
   выедает квоту и не тормозит работу самого хост-проекта). Один слой другой не
   заменяет.

3. **API-ключ на потребителя — тем же env-prefix паттерном, что и сами секреты**
   (`GATEWAY_KEY_<PROJECT>`, как `VK_TOKEN_<NAME>`; constant-time сравнение; в логи
   только имя проекта). Даёт и авторизацию, и per-consumer атрибуцию для квоты/статы
   бесплатно. Родня [#008 secrets-outside-repo](../../../brain_matrica/cross-project-ideas/ideas/008-secrets-outside-repo.md).

4. **Read-only allowlist в v1.** Минимизирует blast radius (read нельзя
   заспамить/забанить за поведение). Write — отдельный guarded слой позже, с per-key
   scope. Покрывает 100% названного («проанализируй/скачай/импортируй» = чтение).

5. **Usage-лог с сохранением параметров запроса** → видимость «кто/что/сколько»
   (страница статистики). Родня [#018 liveness/visibility](../../../brain_matrica/cross-project-ideas/ideas/018-liveness-watchdog-durable-heartbeat.md):
   раз доступ раздан наружу, «молча встало / кто-то жжёт квоту» дороже — нужна
   наблюдаемость на границе.

## Кандидаты-потребители

Любой @valstan-проект, которому нужен VK без своей VK-инфры: GONBA, Sabantuy/Малмыж,
районные сайты (Вмалмыже.рф, ЦДК-Калинино.рф, ДкМалмыж.рф). Ключи уже выписаны на
прод. Контракт — `setka/docs/GATEWAY.md` (base URL, заголовок, примеры curl).

## Связи

[#020 probe-before-build](../../../brain_matrica/cross-project-ideas/ideas/020-probe-before-build.md)
(probe доступности ресурса до постройки шлюза), [#008](../../../brain_matrica/cross-project-ideas/ideas/008-secrets-outside-repo.md)
(секреты вне репо), [#001](../../../brain_matrica/cross-project-ideas/ideas/001-isolated-deploy-ssh-key.md)/[#002](../../../brain_matrica/cross-project-ideas/ideas/002-ssh-deploy-key-rotation.md)
(изоляция/ротация credential — здесь усилено: credential вообще не покидает хост).

Если паттерн полезен — может стать pool-идеей «проект-как-shared-service для
sibling-проектов». SARAFAN — pioneer. Применяй/правь по усмотрению (suggest).

— setka
