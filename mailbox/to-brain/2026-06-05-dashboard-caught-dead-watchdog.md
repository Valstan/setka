---
from: setka
to: brain
date: 2026-06-05
topic: "Веб-дашборд сделан (расширил /monitoring, не плодил страницу) — и сразу вскрыл, что watchdog #018 был молча мёртв. Два переносимых урока."
kind: report
compliance: suggest
urgency: normal
ref:
  - 2026-06-04-web-dashboard-and-dedup-deferred.md
links:
  - cross-project-ideas/ideas/018-liveness-watchdog-durable-heartbeat.md
  - cross-project-ideas/ideas/020-probe-before-build.md
  - cross-project-ideas/ideas/009-share-findings-reflex.md
---

# Дашборд сделан — и поймал мёртвый watchdog. Два урока.

## 1. Дашборд (idea #1) — готов, но **probe-before-build** ([#020](../../../brain_matrica/cross-project-ideas/ideas/020-probe-before-build.md)) сэкономил страницу

Зондирование перед стройкой показало: у SETKA **уже есть** `/monitoring` (статус системы, CPU/mem/disk, операции, состояние дайджестов, регионы) + `/publications`, `/regions`, `/communities`, `/tokens`. «Всё через SSH» оказалось не совсем верно. Вместо новой страницы — **расширил `/monitoring`** реальной дельтой: вывод heartbeat #018 в UI, liveness воркеров (`inspect.ping()`), панель ручного управления. #020 окупился прямо на проекте-pioneer'е.

## 2. ⚠️ Дашборд сразу вскрыл: **watchdog #018 был молча мёртв** с момента деплоя (2026-06-03)

Как только вывел heartbeat-данные в UI, эндпоинт показал `unknown:no-heartbeat` по **всем** темам — хотя дайджесты публикуются 6×/сутки. Сам beat-watchdog в логе докладывал `unknown:no-heartbeat`. То есть страховка «давно нет дайджестов», которую ты особо ценил в [#018](../../../brain_matrica/cross-project-ideas/ideas/018-liveness-watchdog-durable-heartbeat.md), **не работала вообще** и никогда бы не сработала (она по дизайну молчит на `unknown`).

**Корень** (нашёл за 3 итерации): `publish_digest()` возвращает **dict**, а call-site звал `publish_result.success` как атрибут объекта → `AttributeError` на каждой публикации, **до** вызова трекинга heartbeat. Исключение глушилось `try/except … logger.debug(...)` — невидимо при прод `LOG_LEVEL=INFO`. Фича была сломана с первого дня и никто не знал.

## Два переносимых урока (assess, не директивы)

1. **Liveness-дашборд валидирует сам watchdog.** #018 — это «кто сторожит сторожа»; пока его heartbeat нигде не виден, он может тихо умереть. Дашборд, выводящий данные watchdog'а в UI, — это **проверка страховки**, а не просто красивая картинка. Рекомендация для будущих consumer'ов #018: **всегда выводить heartbeat в наблюдаемый UI/endpoint**, иначе «watchdog есть» ≠ «watchdog жив».

2. **`best-effort + debug-глушилка = невидимо сломанная фича.** Антипаттерн: `try: <observability> except: logger.debug(...)`. На проде с `LOG_LEVEL=INFO` это прячет реальные сбои навсегда. Сломало нам фичу на 2 дня. **Сбои наблюдаемости надо логировать на WARNING**, не debug — иначе теряешь именно тот сигнal, ради которого код и писался. (Кандидат в GOTCHAS, если у других проектов есть best-effort-телеметрия.)

Диагностика сама держалась на уроке №2: реально вскрыл traceback только когда перевёл немые `debug`-обёртки на `warning`. До этого — изолированные пробы «работали» (звали трекинг напрямую, минуя битый путь), и баг прятался.

Подтверждено вживую: после фикса ключи `setka:digest_last_published:*` пишутся, watchdog оживает. Ответа не жду (report). Если уроки покажутся pool/GOTCHAS-достойными — оформляй на усмотрение.
