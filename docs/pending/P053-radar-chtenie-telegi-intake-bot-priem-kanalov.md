# ~~📡 Радар — чтение Телеги + intake-бот «приём каналов» (запрос владельца 2026-06-14)~~ ЗАКРЫТО 2026-08-28 — инженерного остатка нет, чужой шаг уже продублирован в пакете

> Запись `P053` реестра [PENDING_FOLLOWUPS](../PENDING_FOLLOWUPS.md). Индекс грепается, запись читается целиком, реестр целиком не читается никогда ([D-097](../../AGENTS.md)).

`⏱ 2026-06-14 · snooze 0 · ✅ ЗАКРЫТО 2026-08-28 по ре-триажу — секция схлопнута в record`

**Почему закрыто (ре-триаж 2026-08-28).** Код на месте и живёт: beat-слоты
`tasks/celery_app.py:2305` `radar-intake-bot` и `:2313` `radar-vk-intake`, модули
`modules/radar/bot_intake.py`, `modules/radar/subscriptions.py`, `modules/radar/vk_intake.py`.
Единственный чужой шаг уже продублирован в «Пакете браузер-верификаций владельцем» строкой
«Intake-бот радара: форварднуть @malm_info_bot пост канала → канал появился в радаре», то есть
секция держалась ради записи, которая существует и без неё. Env восстановимы —
`modules/secrets_bootstrap.py:119-120` содержит `RADAR_BOT_NAME` и `RADAR_BOT_ALLOWED_USERS`.
**Единственный несомый риск вынесен отдельным техдолгом** («🟡 Подписка радара заведена
прод-INSERT-ом — сидера в git нет»): grep `radar_subscriptions` по `scripts/` и
`database/migrations/` не дал ни одного файла-сидера, значит при пересборке БД подписка
valstan→gonba_life теряется молча.

_Ре-триаж 2026-07-27 (pool #033):_ всё построено, задеплоено
и проверено на проде (TG-relay устойчив, intake-бот включён на AFONYA). Единственный остаток —
браузер/TG-проверка владельцем; она перенесена в [«Пакет браузер-верификаций владельцем»](P005-paket-brauzer-verifikaciy-vladelcem.md),
чтобы не держать секцию открытой ради одного чужого шага.

- ✅ **Радар↔Телега починен:** корень — `radar_subscriptions` пуст → поллер видел `sources:0`.
  Восстановил подписку valstan→gonba_life (прод-правка вне git, INSERT). TG-чтение через relay
  исправно. Поллер сразу взял пост + web-push.
- ✅ **Intake-бот построен + ВКЛЮЧЕН** (PR [#235](https://github.com/Valstan/setka/pull/235)–[#238](https://github.com/Valstan/setka/pull/238)): форвард поста канала боту → канал в
  радар. `modules/radar/bot_intake.py` (getUpdates-polling, **молчит чужим** + гейт на allowlist —
  боты публичные с трафиком), сервис `modules/radar/subscriptions.py` (DRY с API). Beat
  `radar-intake-bot` раз в минуту, offset в redis. **На AFONYA** (@malm_info_bot): env
  `RADAR_BOT_NAME=AFONYA` + `RADAR_BOT_ALLOWED_USERS=352096813` (прод-правка вне git).
- ➡️ _Браузер/TG-проверка владельцем_ — перенесена в [«Пакет браузер-верификаций владельцем»](P005-paket-brauzer-verifikaciy-vladelcem.md) (ре-триаж 2026-07-27).
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
