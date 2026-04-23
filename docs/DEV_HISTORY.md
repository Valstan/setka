# История разработки SETKA

## 2026-04-23 — Kirov oblast: восстановление публикации и жёсткая news-фильтрация

- В `modules/kirov_oblast_digest.py` добавлен явный отбор постов-источников только за 72 часа (`OBLAST_LOOKBACK_HOURS`) и только из дайджестоподобных записей с `wall`-ссылками; исключаются очевидные non-news digest-маркеры (`реклама/объявления/дополнительно/addons`) ещё на этапе извлечения ссылок.
- Для `kirov_obl/oblast` включены жёсткие исключения контента после общего пайплайна: реклама/addons, религиозные новости (словарь маркеров), mourning-посты (полностью исключаются из публикации областного дайджеста).
- Добавлена расширенная наблюдаемость запуска: `debug` counters в результате `run_kirov_oblast_digest()` (скан источников, отбор ссылок, отфильтрованные причины, готовые regular/mourning и т.д.).
- «Пустые» сценарии (`нет источников`, `нет ссылок`, `wall.getById вернул пусто`) переведены в мягкий `success=True` с диагностическим `message`, чтобы не маскироваться как аварийные сбои при штатном отсутствии кандидатов.
- Добавлены unit-тесты в `tests/test_kirov_oblast_digest.py` на 72-часовой отбор, digest-source pre-filter и религиозные маркеры.

---

## 2026-04-21 — Антидубли: общий LIP по региону и история целевой группы

- Для каждого региона введён общий контур дедупликации через `work_tables` с технической темой `__region_global__`: `lip/hash` теперь накапливаются не только в тематической таблице, но и в общем региональном контуре.
- При каждом парсинге формируется единый входной `work_table_lip/hash` как объединение **всех** `work_tables` региона, что блокирует повторное попадание одного и того же источника между разными тематиками.
- Добавлен дополнительный фильтр по фактически опубликованной ленте региона: парсятся последние 100 постов целевой группы и из текста дайджестов извлекаются `wall-...` ссылки источников; их LIP тоже попадает в дедуп до сборки нового выпуска.
- Вынесены утилиты `modules/deduplication/digest_history.py` (`build_region_dedup_sets`, `extract_source_lips_from_target_group_posts`, `append_unique_limited`) и добавлены тесты `tests/test_deduplication/test_digest_history.py`.

---

## 2026-04-21 — Дедуп дайджестов: усиление LIP и 90% похожесть текста

- В `modules/vk_monitor/advanced_parser.py` усилен конвейер дедупликации: кроме `lip`/точных hash, добавлена near-duplicate проверка текста на базе 64-bit SimHash с порогом похожести (по умолчанию `0.90`) и ограничением по длине нормализованного текста.
- Персистентный текстовый дедуп теперь использует `work_table.hash` с префиксами `txtfp:`, `txtcore:`, `txtsim:<bucket>:<hash>`; при парсинге эти сигнатуры учитываются вместе с батч-дедупом.
- После публикации в `tasks/parsing_scheduler_tasks.py` и `modules/kirov_oblast_digest.py` в `work_table` сохраняются не только `lip`, но и сигнатуры текста/медиа для будущих прогонов.
- Увеличены окна памяти дедупа в `work_table`: `lip` до 1000, `hash` до 5000 записей (вместо слишком короткой истории, из-за которой старые дубли возвращались).
- Расширены настройки `digest_filters` (`text_similarity_threshold`, `min_rafinad_len_similarity_dedup`) и покрытие тестами для SimHash/исторического дедупа.

---

## 2026-04-21 — Mourning-дайджесты: без любых заголовков и хештегов

- Для mourning-публикаций добавлен единый форматтер `resolve_mourning_digest_format()` в `modules/publisher/postopus_digest_headers.py`, который всегда возвращает пустые `header`, `hashtags` и `local_hashtag`.
- Production-пайплайны `tasks/parsing_scheduler_tasks.py` и `modules/kirov_oblast_digest.py` переведены на этот форматтер, чтобы траурные дайджесты по всем регионам/темам выходили без автоподстановок.
- Обновлены тестовые/диагностические скрипты (`scripts/test_production_pipeline.py`, `scripts/test_parse_run.py`) и убран декоративный префикс `🕯` из вывода тестового скрипта.
- Добавлены тесты `tests/test_publisher/test_postopus_digest_headers.py` и `tests/test_publisher/test_digest_builder.py` на поведение mourning-формата без заголовка и тегов.

---

## 2026-04-21 — Документация: только SSH для прода, без MCP

- Политика доступа: для SETKA **не использовать MCP** в IDE — только **стандартный SSH** на хост с `/home/valstan/SETKA` (обновлён [`REMOTE_ACCESS.md`](REMOTE_ACCESS.md)).
- Удалён [`MCP_SETUP_VSCODE.md`](MCP_SETUP_VSCODE.md); ссылки убраны из [`docs/README.md`](README.md), [`START_HERE.md`](START_HERE.md), [`AI_DEV_GUIDE.md`](AI_DEV_GUIDE.md).

---

## 2026-04-21 — UI «Фильтрация»: возраст постов по темам и правила RegionConfig

- Новая страница `/filtration` (меню **Фильтрация**): настройка `digest_filters` в `region_configs` (defaults + `by_topic`: `max_post_age_hours`, `max_posts_per_digest`, `min_rafinad_len_core_dedup`, `posts_per_community_fetch`), плюс редактирование `black_id`, `delete_msg_blacklist`, `time_old_post`, лимит длины поста, `setka_regim_repost`, `filter_group_by_region_words`, `repost_words_blacklist`.
- API: `/api/filtration/meta`, `/regions`, `GET/PUT /api/filtration/{region_code}`.
- Колонка БД: `region_configs.digest_filters` (JSONB) — миграция `database/migrations/005_region_configs_digest_filters.sql`.
- Парсер и `DigestBuilder` читают эффективные значения через `modules/digest_pipeline_settings.py` (`get_effective_pipeline_settings`).

---

## 2026-04-21 — Парсер дайджестов: дедуп репостов и текста

- **Проблема:** проверка `lip` шла до `clear_copy_history`, поэтому один оригинал, репощенный в разные группы, давал разные id и проходил несколько раз; `work_table_hash` / text dedup в `_filter_post` не использовались.
- **Исправление:** сначала unwrap репоста, затем `lip` против `work_table` и накопленного батча одного прогона; дедуп по `create_text_fingerprint` / `create_text_core_fingerprint` (rafinad ≥ 50) и по сигнатуре вложений `create_media_fingerprint`; пересечение с `work_hash_set` для известных id фото/видео.

---

## 2026-04-21 — Дайджесты: только посты не старше 72 часов

- В `AdvancedVKParser._filter_post` после разворачивания репоста проверяется поле `date` (Unix): если возраст публикации **> 72 ч**, пост отбрасывается (`posts_filtered_old`). Без даты — тоже отброс.
- Константа `DIGEST_MAX_POST_AGE_HOURS = 72` в `modules/vk_monitor/advanced_parser.py`.

---

## 2026-04-20 — Дайджесты: без заголовка «Скорбим», заголовки/хештеги как в Postopus, ссылки [url|название]

- Траурный дайджест: без строки-заголовка; внизу те же хештеги темы и региона, что и у обычного дайджеста этого запуска.
- Заголовок и хештеги: приоритет `RegionConfig.zagolovki` / `heshteg` (данные из Mongo/old_postopus); иначе fallback в `modules/publisher/postopus_digest_headers.py` (в т.ч. «Спортивные новости {регион}:»).
- Источник под постом: ВК-разметка `[https://vk.com/wall…|Название сообщества]`; имена подставляются из `communities` по `group_names`.
- Скрипт миграции: для Лебяжья спорт-заголовок приведён к «Спортивные новости Лебяжье:», исправлена опечатка ключа `reklama` у Советска.

---

## 2026-04-20 — Документация: SSH для удалённого доступа

- Добавлен [`REMOTE_ACCESS.md`](REMOTE_ACCESS.md): единое правило — работа с продом SETKA через SSH на хост с `/home/valstan/SETKA`.
- Обновлены [`START_HERE.md`](START_HERE.md), [`README.md`](README.md), [`AI_DEV_GUIDE.md`](AI_DEV_GUIDE.md). (Позже MCP убран из политики — см. запись 2026-04-21 выше.)

---

## 2026-04-20 — Фикс публикации дайджестов: нормализация group_id + fallback сообществ

### Проблема
- Дайджесты собирались, но публикация в часть регионов срывалась: `vk_group_id` в БД мог быть положительным (после миграций), а `wall.post` для групп требует `owner_id < 0`.
- В ряде регионов для конкретной темы не находились сообщества, из-за чего задача завершалась без попытки парсинга/публикации.

### Решение
- В `modules/publisher/vk_publisher_extended.py` добавлена нормализация ID группы: любые входные `group_id` приводятся к формату owner_id группы (`-abs(group_id)`) для `wall.post` и `wall.repost`.
- В `tasks/parsing_scheduler_tasks.py` добавлен fallback: если нет активных сообществ по `theme`, задача берёт все активные сообщества региона вместо мгновенного отказа.

### Проверка
- Добавлены unit-тесты `tests/test_publisher/test_vk_publisher_extended.py`:
  - нормализация positive/negative ID;
  - проверка `owner_id` для `publish_digest`;
  - проверка `group_id`/`object` для `publish_repost`.
- Локально: `pytest tests/test_publisher/test_vk_publisher_extended.py -q` → **3 passed**.

---

## 2026-04-20 — Scheduler: запуск только для валидных регионов

### Проблема
- `run_all_regions_theme` ставил задачи на все активные регионы, включая регионы без `RegionConfig`, без `vk_group_id` или без активных сообществ.
- Это давало «шумные» прогоны с быстрыми отказами и мешало диагностике реальных публикаций.

### Решение
- В `tasks/parsing_scheduler_tasks.py` ужесточён отбор регионов в `run_all_regions_theme(theme)`:
  - регион активен;
  - есть `vk_group_id`;
  - существует `RegionConfig` по `region_code`;
  - есть активные сообщества (по теме или хотя бы любые активные в регионе).

### Проверка
- Добавлен тест `tests/test_scheduler/test_parsing_scheduler_tasks.py` на постановку задач только по отобранным регионам.
- Регрессия publisher-тестов сохранена.
- Локально: `pytest tests/test_scheduler/test_parsing_scheduler_tasks.py tests/test_publisher/test_vk_publisher_extended.py -q` → **4 passed**.

---

## 2026-04-16 — Copy-by-setka: слово «репост», 10 постов / 10 lip, источник по умолчанию

- Источник по умолчанию: группа [copy_by_setka](https://vk.com/copy_by_setka), ID **-167381590** (переопределяется `COPY_SETKA_SOURCE_GROUP_ID`).
- За один запуск — **один** новый пост; `wall.get` не более **10** последних; в `lip` хранится не больше **10** идентификаторов.
- Если в поле `text` есть **«репост»** — `wall.repost` цели из `copy_history` или вложения `wall`; иначе — копия текста и вложений (с разворачиванием `copy_history` через `clear_copy_history`).
- `COPY_SETKA_DISABLED=1` — полностью отключить хаб.

---

## 2026-04-16 — Сетевой хаб `copy` / `setka` + пул БД + wall.repost

### Задача
- Расписание `postopus-copy-setka-07/37` вызывало `parse_and_publish_theme(copy, setka)`, но в БД не было `RegionConfig` для псевдо-региона `copy` — задача сразу выходила с ошибкой.
- Нужно: раз в ~30 мин читать **одну** группу-источник и при появлении **свежей** записи (не в lip, не старше порога) **репостить или копировать** на главные стены активных регионов.

### Решение
- Новый модуль `modules/copy_setka_network.py` + параметры только из **env** (`COPY_SETKA_*` в `/etc/setka/setka.env`), без обязательного `RegionConfig`.
- Ветка в `tasks/parsing_scheduler_tasks.py`: при `region_code=='copy'` и `theme=='setka'` выполняется этот модуль; дедуп по `WorkTable(copy,setka).lip`.
- `VKPublisher.publish_repost`: параметр VK API — **`object`**, не `repost`; для групп передаётся `group_id`.
- Пул asyncpg: по умолчанию **меньше** (`DB_POOL_SIZE=3`, `DB_MAX_OVERFLOW=5`, `pool_recycle`), чтобы реже упираться в `max_connections` на VPS.

### Почему «перестали идти дайджесты» (кратко)
- Подтверждено: конфликт event loop + исчерпание слотов PostgreSQL мешали Celery-задачам; исправлено `run_coro` и перезапуском воркеров.
- Если снова «тишина» — проверить **VK**: `scripts/check_vk_token.py`, лимиты API, и таблицу `parsing_stats` / логи воркера.

---

## 2026-04-16 — Прод: парсинг/постинг не шли (Celery + БД)

### Симптомы
- По расписанию шли задачи (beat в порядке), но пайплайн парсинг → фильтр → постинг фактически не выполнялся.
- В `celery-worker.log`: `asyncpg.exceptions.TooManyConnectionsError` и `Future ... attached to a different loop` в `run_all_regions_theme` / SQLAlchemy.

### Причины
1. **`run_all_regions_theme`** создавал **новый event loop** на каждый запуск, тогда как остальные Celery-задачи используют **`run_coro`** (один loop на процесс воркера). Глобальный async engine/asyncpg оказывался привязан к другому loop → ошибка цикла и некорректное закрытие соединений.
2. **`parse_and_publish_theme`** после прошлого рефакторинга гонял async в **отдельном потоке** с отдельным loop — тот же конфликт с общим пулом соединений.
3. В **`database/connection.py`** был продублирован блок создания engine (мертвый код, риск путаницы при правках).

### Решения
- Все async-вызовы в `tasks/parsing_scheduler_tasks.py` переведены на **`run_coro`** (как в `correct_workflow_tasks` и `celery_app`).
- Удалён дубликат конфигурации в **`database/connection.py`**.

### Прод-деплой
- `git pull` на VPS, `systemctl restart setka setka-celery-worker setka-celery-beat`.

---

## 2026-04-13 — Исправления дайджеста: форматирование, токены, mourning

### Проблемы
1. Посты обрезались на полуслове в дайджесте
2. Публикация шла не тем токеном (Vita вместо Valstan)
3. Дайджест был "сплошным мясом" без разделения новостей
4. Траурные новости (СВО, смерть) перемешивались с позитивными

### Решения
- **No truncation**: посты, не влезающие целиком, пропускаются (на следующую итерацию)
- **Формат old_postopus**: `✍ текст` → ссылка-источник `[url|название]` → пустая строка (раньше `@url (source)`)
- **Token roles**: `VK_PUBLISH_TOKEN_NAME=VALSTAN` — только VALSTAN может публиковать
- **SentimentAnalyzer**: mourning detection (погиб, умер, СВО, прощание...)
- **DigestSplitter**: разделяет post-ы на mourning/regular перед билдингом
- **Mourning digest**: отдельный пост без заголовка (см. актуальное поведение выше)

### Файлы изменены
- `config/runtime.py` — VK_PUBLISH_TOKEN_NAME, get_publish_token(), validate_publish_token()
- `modules/publisher/digest_builder.py` — ✍ маркеры, no truncation, _format_post_entry()
- `modules/ai_analyzer/sentiment_analyzer.py` — MOURNING_MARKERS, label='mourning'
- `modules/publisher/digest_splitter.py` — НОВЫЙ: разделение по тональности
- `tasks/parsing_scheduler_tasks.py` — интеграция DigestSplitter в production pipeline
- `scripts/test_parse_run.py` — тест с разделением и двумя публикациями

### Результат теста
- 36 постов: 3 mourning → пост #580, 7 regular → пост #579
- Оба опубликованы через VALSTAN токен
- https://vk.com/wall-137760500_579 (обычный)
- https://vk.com/wall-137760500_580 (mourning)
