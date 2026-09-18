# 🧩 Brain-директивы 2026-06-14 (recommend, probe-first)

> Запись `P051` реестра [PENDING_FOLLOWUPS](../PENDING_FOLLOWUPS.md). Индекс грепается, запись читается целиком, реестр целиком не читается никогда ([D-097](../../AGENTS.md)).

`⏱ 2026-06-14 · snooze 0 · parked` — **ре-триаж 2026-07-27 (pool #033):** сетевая рассылка построена
и задеплоена, 413-загрузка и потеря картинок починены (обе — с probe и прод-проверкой). Остаток
двоякий и **оба хвоста чужие**: браузер-проверки владельцем → перенесены в [«Пакет
браузер-верификаций владельцем»](P005-paket-brauzer-verifikaciy-vladelcem.md); **генератор обложек** ждёт
фона от владельца (probe показал 16/16 `can_set`, но вход не определён — до прихода фона сборщик не
строим, это записано в самой директиве). Условие расконсервации: **владелец прислал фон обложки**.

- ✅ **Сетевая рассылка + внутренний планировщик — ПОСТРОЕНА и ЗАДЕПЛОЕНА 2026-06-14**
  (probe-ответ [#242](https://github.com/Valstan/setka/pull/242) → MVP [#243](https://github.com/Valstan/setka/pull/243) → QA-фиксы [#246](https://github.com/Valstan/setka/pull/246)).
  `modules/broadcast/` (dispatcher + service) + миграция 044 (`broadcast_campaigns`/`_targets`/`_publications`)
  + beat `broadcast-dispatch` (раз в минуту) + `broadcast-watchdog` (#018) + API/UI `/broadcast`.
  Канон владельца соблюдён (свой беат `wall.post` немедленно, НЕ VK-отложка); переиспользует
  `VKPublisher.create_with_policy` + ad-CRM-примитивы; idempotency per-(цель,прогон) ON CONFLICT claim
  + reclaim stale-pending; throttle ≥5с; повтор N раз; per-target изоляция. ➡️ _Браузер-проверка
  перенесена в [«Пакет браузер-верификаций владельцем»](P005-paket-brauzer-verifikaciy-vladelcem.md)_ (ре-триаж
  2026-07-27); опц. вариация per-target (`vary_per_target` — forward-compat поле, дизайн за brain/владельцем).
- ✅ **Починка загрузки картинок (413) + UX удаления + кликабельные ссылки 2026-06-18**
  (PR [#262](https://github.com/Valstan/setka/pull/262) + прод-правка nginx вне git). Загрузка >1 МБ
  падала с 413: у myjino HTTPS обрывается на edge-прокси → трафик идёт на **nginx:80 (Block 3)**, а
  не на 443; у Block 3 не был задан `client_max_body_size` (дефолт 1 МБ). **Фикс: `client_max_body_size
  20m` на уровне `http` в `/etc/nginx/nginx.conf`** (наследуют все блоки; бэкап `nginx.conf.bak-413`) —
  проверено по реальному пути (2/8 МБ → не 413). Шаблон: явная красная кнопка удаления + бейдж «в посте»
  + подсказка про `https://`-префикс (VK сам линкует). ➡️ _Подтверждение «>1 МБ грузится» перенесено в
  [«Пакет браузер-верификаций владельцем»](P005-paket-brauzer-verifikaciy-vladelcem.md)_ (ре-триаж 2026-07-27).
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
  `vk_wall_photo_upload`. +новые тесты (per-target attach, parse-map, text-only/no-rebuild). ➡️ _Проверка
  «картинка в опубликованном посте» перенесена в [«Пакет браузер-верификаций владельцем»](P005-paket-brauzer-verifikaciy-vladelcem.md)_
  (ре-триаж 2026-07-27).
- ⏳ **Генератор обложек сообществ** (`...2026-06-14-community-cover-template-generator.md`): шаблон-сборщик
  cover'ов (фон от владельца → название+брендинг → upload), пилот Верхошижемье. **Probe cover-API выполнен
  2026-06-14** ([#244](https://github.com/Valstan/setka/pull/244), `scripts/probe_cover_api.py`, ответ brain
  `mailbox/to-brain/2026-06-14-community-cover-api-probe.md`): **16/16 пабликов `can_set` через user-токен
  VALSTAN** (владелец админ везде — G19-барьер НЕ материализовался), 11/16 с обложкой (референсы), 5 без
  (вкл. пилот Верхошижемье). **Мяч у brain↔владельца:** brain собирает промт фона по референсам → владелец
  генерит фон → SARAFAN строит сборщик (Pillow 1920×768 + название + брендинг → `saveOwnerCoverPhoto`).
  До прихода фона сборщик не строить (вход не определён).
