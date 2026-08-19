# Метрики постов и рейтинг популярности — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Завести в `collected_post_audit` дату поста и метрики ВК, обновлять их фоновой таской и показать топ-N по параметризованному рейтингу — не меняя того, что публикуется.

**Architecture:** Шесть колонок в существующей shadow-таблице аудита сбора; дату пишет сам сбор (она уже в словаре поста), метрики — Celery-таска батчами по 100 через `wall.getById`. Рейтинг — чистая функция с показателем степени наружу, витрина сравнивает три значения этого показателя на боевых данных, чтобы владелец выбрал одно глазами.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy (async), Celery + beat, PostgreSQL, `vk_api`, pytest, Bootstrap 5 в шаблонах Jinja2.

**Spec:** [`docs/superpowers/specs/2026-08-19-post-metrics-and-rating-design.md`](../specs/2026-08-19-post-metrics-and-rating-design.md)

## Global Constraints

- **Публикация не меняется ни на шаг.** Ни один шаг плана не трогает `advanced_parser.py`, `post_popularity`, редакционные фильтры и путь публикации. Заход только заводит измерение.
- **`NULL` ≠ `0` во всех новых колонках.** `NULL` = «не мерили», `0` = «ноль реакций». Дефолтов `0` не ставить нигде — ни в миграции, ни в модели, ни в парсере ответа ВК.
- **`post_popularity` в `utils/post_utils.py` не изменять** — на ней висит сортировка ленты в `advanced_parser.py`.
- Локальный pytest: `--basetemp=<scratchpad>/pt -p no:cacheprovider`, иначе падает правами.
- `PRE_COMMIT_HOME` обязателен для `git commit`, не только для `pre-commit run`.
- Commit-messages — Conventional Commits, гейт в pre-commit это проверяет.
- Многострочный код писать через инструмент записи файла, **не** через `cat <<'EOF'` — heredoc падает «unexpected EOF» на backticks и кавычках.
- Миграции на прод применяются вручную через `/reliz` под `AskUserQuestion`; `migrate.py up` использовать нельзя (бухгалтерия миграций разошлась с реальностью, 🟡 в PENDING).

---

## Файловая структура

| файл | ответственность |
|---|---|
| `config/classifier.py` (M) | `get_rating_views_alpha()` — чтение `RATING_VIEWS_ALPHA` из env |
| `utils/post_utils.py` (M) | `post_rating()` — чистая функция рейтинга, без конфига и без БД |
| `database/migrations/080_post_metrics.sql` (C) | шесть колонок + индекс |
| `database/models_extended.py` (M) | те же поля в модели `CollectedPostAudit` + `to_dict` |
| `modules/curation/collection_audit.py` (M) | `_snapshot` пишет `published_at` из `post['date']` |
| `modules/vk_monitor/post_metrics.py` (C) | общий батч-фетчер `wall.getById` — единственное место, где парсится ответ ВК по метрикам |
| `modules/ad_cabinet/publication_stats.py` (M) | переводится на общий фетчер, своя политика токенов остаётся |
| `modules/classifier/metrics_refresh.py` (C) | отбор кандидатов на обновление + запись метрик (вся логика, без Celery) |
| `tasks/celery_app.py` (M) | таска-обёртка `refresh_post_metrics` + запись в beat |
| `modules/classifier/rating.py` (C) | `top_by_rating()` — топ-N внутри разрешённых вердиктом |
| `web/api/classifier_review.py` (M) | эндпоинт витрины `GET /rating/top` |
| `web/templates/classifier.html` (M) | блок витрины на странице |

Тесты — по одному файлу на задачу, пути в каждой задаче.

---

### Task 1: Чистая функция рейтинга

**Files:**
- Modify: `config/classifier.py`
- Modify: `utils/post_utils.py`
- Test: `tests/test_classifier/test_post_rating.py` (create)

**Interfaces:**
- Consumes: `post_popularity(views, likes, comments, reposts) -> float` (существует, `utils/post_utils.py:30`)
- Produces:
  - `post_rating(views: Optional[int], likes: Optional[int], comments: Optional[int], reposts: Optional[int], *, alpha: float) -> Optional[float]`
  - `vk_post_datetime(ts: Any) -> Optional[datetime]` — unix-время поста ВК в наивный UTC
  - `get_rating_views_alpha() -> float` в `config/classifier.py`

**Ruling пре-флайта:** `vk_post_datetime` живёт здесь, а не двумя копиями в T3 и T4.
План требовал одинаковый `_published_at` в `collection_audit.py` и в `post_metrics.py`,
что противоречит его же доводу в T4 («вторая копия разошлась бы молча»). Два дословных
парсера даты — тот же дефект на уровень ниже общего фетчера.

**Почему `alpha` — обязательный keyword-аргумент, а не чтение конфига внутри.** `utils/post_utils.py` — низкий уровень без зависимостей на `config/`. Чистая функция остаётся чистой и тестируется без env; конфиг читает вызывающий.

- [ ] **Step 1: Написать падающий тест**

Создать `tests/test_classifier/test_post_rating.py`:

```python
"""Рейтинг поста для отбора в корневую группу (звено 5, шаг 1).

Функция чистая и конфига не читает — alpha приходит аргументом. Поэтому
здесь же живёт гейт «при alpha=0.5 рейтинг вырождается в нынешнюю
post_popularity»: это единственная гарантия, что мы не поменяли молча
сортировку ленты, которая на post_popularity висит.
"""

import pytest

from utils.post_utils import post_popularity, post_rating


def test_alpha_half_reproduces_post_popularity():
    for views, likes, comments, reposts in [
        (100, 5, 2, 1),
        (1224, 7, 0, 0),
        (0, 3, 0, 0),
        (0, 0, 0, 0),
        (17, 7, 0, 0),
    ]:
        assert post_rating(views, likes, comments, reposts, alpha=0.5) == pytest.approx(
            post_popularity(views, likes, comments, reposts)
        )


def test_lower_alpha_lifts_wide_reach_over_small_group():
    # Числа из спеки: районный хит против маленькой группы.
    big = (10000, 100, 0, 0)
    small = (20, 12, 0, 0)
    assert post_rating(*big, alpha=0.5) < post_rating(*small, alpha=0.5)
    assert post_rating(*big, alpha=0.25) > post_rating(*small, alpha=0.25)


def test_alpha_zero_is_pure_engagement():
    assert post_rating(10000, 100, 0, 0, alpha=0.0) == pytest.approx(100.0)
    assert post_rating(20, 12, 0, 0, alpha=0.0) == pytest.approx(12.0)


def test_views_none_gives_no_score():
    # Посты без views (10% выборки) рейтинга не получают: делитель схлопнулся
    # бы в 1, и пост без единого просмотра обогнал бы районный хит.
    assert post_rating(None, 50, 0, 0, alpha=0.25) is None


def test_missing_reactions_count_as_zero_not_as_missing():
    # None у реакций — это «ВК не прислал поле», трактуем нулём: в отличие от
    # views оно не стоит в знаменателе и рейтинг не искажает.
    assert post_rating(100, None, None, None, alpha=0.25) == pytest.approx(0.0)


def test_weights_are_the_project_convention():
    # лайк 1 · коммент 2 · репост 3
    assert post_rating(0, 1, 0, 0, alpha=0.0) == pytest.approx(1.0)
    assert post_rating(0, 0, 1, 0, alpha=0.0) == pytest.approx(2.0)
    assert post_rating(0, 0, 0, 1, alpha=0.0) == pytest.approx(3.0)


def test_alpha_from_config_defaults_to_quarter(monkeypatch):
    from config.classifier import get_rating_views_alpha

    monkeypatch.delenv("RATING_VIEWS_ALPHA", raising=False)
    assert get_rating_views_alpha() == pytest.approx(0.25)

    monkeypatch.setenv("RATING_VIEWS_ALPHA", "0.5")
    assert get_rating_views_alpha() == pytest.approx(0.5)


def test_vk_post_datetime_converts_unix_to_naive_utc():
    from datetime import datetime

    from utils.post_utils import vk_post_datetime

    assert vk_post_datetime(1787136000) == datetime(2026, 8, 19, 10, 40)


def test_vk_post_datetime_returns_none_on_anything_broken():
    # Подставленная «сейчас» обманула бы отсев по старости в нашу пользу.
    from utils.post_utils import vk_post_datetime

    for bad in (None, 0, "", "не число", [], 10**20):
        assert vk_post_datetime(bad) is None, f"ts={bad!r}"


def test_broken_alpha_env_falls_back_to_default(monkeypatch):
    # Опечатка в env не должна ронять отбор — ошибочное значение читается
    # как «дефолт», а не как исключение посреди волны публикации.
    from config.classifier import get_rating_views_alpha

    monkeypatch.setenv("RATING_VIEWS_ALPHA", "не число")
    assert get_rating_views_alpha() == pytest.approx(0.25)
```

- [ ] **Step 2: Прогнать тест, убедиться что падает**

Run: `./venv/Scripts/python.exe -m pytest tests/test_classifier/test_post_rating.py -q --basetemp=C:/Temp/claude/D--PROGRAMMING-setka/fb2d97a1-a6d0-4c47-8162-2df4d83cc462/scratchpad/pt -p no:cacheprovider`

Expected: FAIL, `ImportError: cannot import name 'post_rating'`

- [ ] **Step 3: Добавить `get_rating_views_alpha` в `config/classifier.py`**

Дописать в конец файла:

```python
# Показатель степени при просмотрах в рейтинге отбора (звено 5, шаг 1).
# score = engagement / (views + 1) ** alpha
#   alpha = 0.5 — нынешняя post_popularity (рейтинг вовлечённости: маленькая
#                 группа с высоким откликом обгоняет районный хит)
#   alpha = 0.25 — дефолт: охват весомее, но не решает в одиночку
#   alpha = 0   — чистый абсолютный охват
# Наружу вынесен сознательно: это редакционная настройка, её подбирают на
# данных через витрину /api/classifier-review/rating/top, а не в коде.
RATING_VIEWS_ALPHA_DEFAULT = 0.25


def get_rating_views_alpha() -> float:
    """Показатель степени при просмотрах (env ``RATING_VIEWS_ALPHA``).

    Нечитаемое значение — это дефолт, а не исключение: опечатка в env не
    должна ронять отбор посреди волны публикации.
    """
    raw = (os.getenv("RATING_VIEWS_ALPHA") or "").strip()
    if not raw:
        return RATING_VIEWS_ALPHA_DEFAULT
    try:
        return float(raw)
    except ValueError:
        return RATING_VIEWS_ALPHA_DEFAULT
```

Дописать строку в docstring-блок `Env vars:` в шапке файла:

```
  RATING_VIEWS_ALPHA=0.25     # показатель степени при просмотрах в рейтинге
                              # отбора; 0.5 = нынешняя post_popularity, 0 = охват
```

- [ ] **Step 4: Добавить `post_rating` в `utils/post_utils.py`**

Дописать сразу после `post_popularity` (не трогая её саму):

```python
def post_rating(
    views: Optional[int],
    likes: Optional[int],
    comments: Optional[int],
    reposts: Optional[int],
    *,
    alpha: float,
) -> Optional[float]:
    """Рейтинг поста для отбора в корневую группу (звено 5, шаг 1).

    ``score = (likes + 2*comments + 3*reposts) / (views + 1) ** alpha``

    Отличается от ``post_popularity`` ровно одним: показатель степени при
    просмотрах вынесен наружу. При ``alpha=0.5`` совпадает с ней с точностью
    до плавающей запятой (гейт в тестах) — то есть это не новая формула, а
    та же с ручкой. ``alpha`` меньше 0.5 поднимает охват, больше — отклик.

    ``views is None`` → ``None``, и это не то же самое, что ноль: пост, у
    которого просмотры не измерены, обогнал бы районный хит, потому что
    делитель схлопнулся бы в единицу. Такие посты уходят в хвост очереди,
    а не наверх. Реакции же ``None`` считаются нулём — они в знаменателе не
    стоят и рейтинг не искажают.

    ``alpha`` — обязательный keyword-аргумент, а не чтение конфига внутри:
    модуль низкоуровневый и на ``config/`` не завязан. Читает вызывающий
    (``config.classifier.get_rating_views_alpha``).
    """
    if views is None:
        return None
    engagement = (likes or 0) + (comments or 0) * 2 + (reposts or 0) * 3
    return engagement / ((int(views) + 1) ** alpha)
```

Там же добавить общий конвертер времени поста:

```python
def vk_post_datetime(ts: Any) -> Optional[datetime]:
    """Unix-время поста ВК (поле ``date``) → наивный UTC, как времена в БД.

    Одно место на весь проект: этот разбор нужен и аудиту сбора, и фетчеру
    метрик, а две копии разошлись бы молча — ровно тот класс, из-за которого
    D-024 сводил три копии ``_call_api`` в один клиент.

    ``datetime.utcfromtimestamp`` не используем: в 3.12 она deprecated. Любое
    битое значение — ``None``, а не «сейчас»: подставленная дата обманула бы
    отсев по старости в нашу пользу.
    """
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).replace(tzinfo=None)
    except (TypeError, ValueError, OSError, OverflowError):
        return None
```

В шапке `utils/post_utils.py` поправить импорты:

```python
from datetime import datetime, timezone
from typing import Any, Dict, Optional
```

- [ ] **Step 5: Прогнать тест, убедиться что проходит**

Run: `./venv/Scripts/python.exe -m pytest tests/test_classifier/test_post_rating.py -q --basetemp=C:/Temp/claude/D--PROGRAMMING-setka/fb2d97a1-a6d0-4c47-8162-2df4d83cc462/scratchpad/pt -p no:cacheprovider`

Expected: PASS, 10 тестов

- [ ] **Step 6: Коммит**

```bash
git add config/classifier.py utils/post_utils.py tests/test_classifier/test_post_rating.py
git commit -m "feat(rating): функция рейтинга с показателем степени наружу"
```

---

### Task 2: Миграция 080 и модель

**Files:**
- Create: `database/migrations/080_post_metrics.sql`
- Modify: `database/models_extended.py` (класс `CollectedPostAudit`, ~строка 626)
- Test: `tests/test_classifier/test_audit_metrics_columns.py` (create)

**Interfaces:**
- Produces: поля `CollectedPostAudit.published_at`, `.views`, `.likes`, `.comments`, `.reposts`, `.metrics_updated_at` (все `Optional`); ключи с теми же именами в `to_dict()`

- [ ] **Step 1: Написать падающий тест**

Создать `tests/test_classifier/test_audit_metrics_columns.py`:

```python
"""Колонки метрик в аудите сбора (миграция 080, звено 5 шаг 1).

Отдельный тест на то, что поля НЕ имеют дефолта 0. Это не педантизм:
панель в #493 рисовала «токенов: 0» там, где ушло 1.4 млн, ровно потому,
что пустое поле читалось нулём. Отсутствующая метрика честнее неполной.
"""

from database.models_extended import CollectedPostAudit

METRIC_FIELDS = ("views", "likes", "comments", "reposts")


def test_model_has_metric_columns():
    cols = CollectedPostAudit.__table__.columns
    for name in METRIC_FIELDS + ("published_at", "metrics_updated_at"):
        assert name in cols, f"нет колонки {name}"


def test_metrics_are_nullable_without_zero_default():
    cols = CollectedPostAudit.__table__.columns
    for name in METRIC_FIELDS + ("published_at", "metrics_updated_at"):
        col = cols[name]
        assert col.nullable is True, f"{name} обязана быть NULL-able"
        assert col.default is None, f"{name}: NULL значит «не мерили», дефолта быть не должно"
        assert col.server_default is None, f"{name}: server_default сделал бы «не мерили» нулём"


def test_to_dict_exposes_metrics_as_none_when_unmeasured():
    row = CollectedPostAudit(lip="1_2", region_code="mi", decision="kept")
    d = row.to_dict()
    for name in METRIC_FIELDS:
        assert d[name] is None, f"{name} не должен превращаться в 0 при сериализации"
    assert d["published_at"] is None
```

- [ ] **Step 2: Прогнать тест, убедиться что падает**

Run: `./venv/Scripts/python.exe -m pytest tests/test_classifier/test_audit_metrics_columns.py -q --basetemp=C:/Temp/claude/D--PROGRAMMING-setka/fb2d97a1-a6d0-4c47-8162-2df4d83cc462/scratchpad/pt -p no:cacheprovider`

Expected: FAIL, `нет колонки views`

- [ ] **Step 3: Написать миграцию**

Создать `database/migrations/080_post_metrics.sql`:

```sql
-- 080: метрики поста и дата публикации в аудит сбора — звено 5, шаг 1.
--
-- Целевая механика звена 5 (формулировка владельца 2026-08-19): мешок
-- допущенных → отсев по старости 72 часа → выбор под тему из расписания →
-- рейтинг по популярности → с верхушки набирается один пост в корневую
-- группу. Ни рейтинга, ни данных под него в базе не было вовсе: в
-- collected_post_audit нет ни метрик, ни даты самого поста.
--
-- published_at — дата поста В ВК (поле date из API), а НЕ момент нашей
-- публикации. 72 часа отсчитываются как возраст поста; то, что мы уже
-- опубликовали, отслеживается отдельно, в work_tables.lip.
--
-- ВСЕ колонки NULL-able и БЕЗ дефолта 0 — сознательно. NULL значит «не
-- мерили», 0 — «ноль реакций». Дефолт 0 соврал бы ровно так, как соврала
-- панель классификатора в #493: tokens_estimate был пуст у 2238 вердиктов
-- из 2238, а рисовалось «токенов: 0» там, где ушло ~1.4 млн токенов.
--
-- Backfill не нужен: published_at для новых строк пишет сам сбор (date уже
-- лежит в словаре поста), а для существующих её заполнит таска обновления
-- метрик — wall.getById возвращает date попутно.

ALTER TABLE collected_post_audit
    ADD COLUMN IF NOT EXISTS published_at       TIMESTAMP NULL,
    ADD COLUMN IF NOT EXISTS views              INTEGER   NULL,
    ADD COLUMN IF NOT EXISTS likes              INTEGER   NULL,
    ADD COLUMN IF NOT EXISTS comments           INTEGER   NULL,
    ADD COLUMN IF NOT EXISTS reposts            INTEGER   NULL,
    ADD COLUMN IF NOT EXISTS metrics_updated_at TIMESTAMP NULL;

-- Окно отбора — «посты моложе 72 часов». Индекс под него.
CREATE INDEX IF NOT EXISTS ix_collected_post_audit_published_at
    ON collected_post_audit (published_at);

-- Откат:
-- DROP INDEX IF EXISTS ix_collected_post_audit_published_at;
-- ALTER TABLE collected_post_audit
--     DROP COLUMN IF EXISTS published_at,
--     DROP COLUMN IF EXISTS views,
--     DROP COLUMN IF EXISTS likes,
--     DROP COLUMN IF EXISTS comments,
--     DROP COLUMN IF EXISTS reposts,
--     DROP COLUMN IF EXISTS metrics_updated_at;
```

- [ ] **Step 4: Добавить поля в модель**

В `database/models_extended.py`, в классе `CollectedPostAudit`, сразу после строки `collected_at = Column(...)`:

```python
    # --- Метрики поста и его дата (миграция 080, звено 5 шаг 1) ---------------
    # published_at — дата поста В ВК, а не момент нашей публикации: 72 часа
    # отсева по старости считаются как возраст поста. Что опубликовали мы —
    # в work_tables.lip.
    published_at = Column(DateTime, nullable=True)
    # NULL ≠ 0. NULL — «не мерили», 0 — «ноль реакций». Дефолта нет намеренно
    # (см. комментарий миграции 080 и урок #493).
    views = Column(Integer, nullable=True)
    likes = Column(Integer, nullable=True)
    comments = Column(Integer, nullable=True)
    reposts = Column(Integer, nullable=True)
    metrics_updated_at = Column(DateTime, nullable=True)
```

В том же классе, в `to_dict()`, перед `return`-скобкой добавить ключи:

```python
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "views": self.views,
            "likes": self.likes,
            "comments": self.comments,
            "reposts": self.reposts,
            "metrics_updated_at": (
                self.metrics_updated_at.isoformat() if self.metrics_updated_at else None
            ),
```

- [ ] **Step 5: Прогнать тест, убедиться что проходит**

Run: `./venv/Scripts/python.exe -m pytest tests/test_classifier/test_audit_metrics_columns.py -q --basetemp=C:/Temp/claude/D--PROGRAMMING-setka/fb2d97a1-a6d0-4c47-8162-2df4d83cc462/scratchpad/pt -p no:cacheprovider`

Expected: PASS, 3 теста

- [ ] **Step 6: Коммит**

```bash
git add database/migrations/080_post_metrics.sql database/models_extended.py tests/test_classifier/test_audit_metrics_columns.py
git commit -m "feat(audit): миграция 080 — дата поста и метрики ВК в аудит сбора"
```

---

### Task 3: Сбор пишет дату поста

**Files:**
- Modify: `modules/curation/collection_audit.py` (`_snapshot`, ~строка 88)
- Test: `tests/test_classifier/test_collection_audit.py` (дописать в существующий)

**Interfaces:**
- Consumes: `CollectedPostAudit.published_at` (Task 2)
- Produces: ключ `"published_at"` типа `Optional[datetime]` в словарях из `build_audit_records()`

- [ ] **Step 1: Написать падающий тест**

Дописать в конец `tests/test_classifier/test_collection_audit.py`:

```python
def test_snapshot_records_vk_post_date():
    """published_at берётся из поля date поста ВК (unix) и хранится наивным UTC.

    Дата нужна для отсева по старости: 72 часа считаются как ВОЗРАСТ ПОСТА,
    а не как время, прошедшее с нашего сбора. Собрать пост можно сильно позже
    его публикации, и тогда collected_at соврал бы в нашу пользу.
    """
    from datetime import datetime

    from modules.curation.collection_audit import build_audit_records

    post = {"owner_id": -100, "id": 7, "text": "текст", "date": 1787136000}
    records = build_audit_records(
        region_code="mi", theme="novost", region_config=None,
        collected=[post], kept=[post],
    )
    assert len(records) == 1
    assert records[0]["published_at"] == datetime(2026, 8, 19, 10, 40)


def test_snapshot_without_date_leaves_published_at_none():
    """Нет поля date → None, а не «сейчас»: подставленная дата обманула бы отсев."""
    from modules.curation.collection_audit import build_audit_records

    post = {"owner_id": -100, "id": 8, "text": "текст"}
    records = build_audit_records(
        region_code="mi", theme="novost", region_config=None,
        collected=[post], kept=[post],
    )
    assert records[0]["published_at"] is None


def test_snapshot_with_broken_date_leaves_published_at_none():
    """Битое значение date не роняет аудит — он никогда не валит сбор."""
    from modules.curation.collection_audit import build_audit_records

    for bad in ("не число", None, [], 10**20):
        post = {"owner_id": -100, "id": 9, "text": "текст", "date": bad}
        records = build_audit_records(
            region_code="mi", theme="novost", region_config=None,
            collected=[post], kept=[post],
        )
        assert records[0]["published_at"] is None, f"date={bad!r}"
```

- [ ] **Step 2: Прогнать тест, убедиться что падает**

Run: `./venv/Scripts/python.exe -m pytest tests/test_classifier/test_collection_audit.py -q -k published_at --basetemp=C:/Temp/claude/D--PROGRAMMING-setka/fb2d97a1-a6d0-4c47-8162-2df4d83cc462/scratchpad/pt -p no:cacheprovider`

Expected: FAIL, `KeyError: 'published_at'`

- [ ] **Step 3: Реализовать**

**Ruling пре-флайта: своего конвертера здесь НЕ заводим** — берём общий
`vk_post_datetime` из Task 1. План изначально требовал дословную копию такого же
парсера и в этом файле, и в `post_metrics.py` (Task 4), что противоречит доводу
самого плана против копий.

В `modules/curation/collection_audit.py` добавить импорт в шапку:

```python
from utils.post_utils import vk_post_datetime
```

В `_snapshot` добавить ключ в возвращаемый словарь, сразу после `"post_url"`:

```python
        "published_at": vk_post_datetime(post.get("date")),
```

- [ ] **Step 4: Прогнать тесты, убедиться что проходят**

Run: `./venv/Scripts/python.exe -m pytest tests/test_classifier/test_collection_audit.py -q --basetemp=C:/Temp/claude/D--PROGRAMMING-setka/fb2d97a1-a6d0-4c47-8162-2df4d83cc462/scratchpad/pt -p no:cacheprovider`

Expected: PASS, все тесты файла (старые + 3 новых)

- [ ] **Step 5: Коммит**

```bash
git add modules/curation/collection_audit.py tests/test_classifier/test_collection_audit.py
git commit -m "feat(audit): сбор записывает дату поста ВК в published_at"
```

---

### Task 4: Общий батч-фетчер метрик

**Files:**
- Create: `modules/vk_monitor/post_metrics.py`
- Modify: `modules/ad_cabinet/publication_stats.py` (`_build_default_fetcher`, строки 30-73)
- Test: `tests/test_vk_monitor/test_post_metrics.py` (create)

**Interfaces:**
- Produces:
  - `Ref = Tuple[int, int]` — `(owner_id, post_id)`, `owner_id` со знаком
  - `parse_metrics_items(items: Iterable[dict]) -> Dict[Ref, Dict[str, Any]]` — чистая, без сети
  - `fetch_metrics_for_token(api, refs: Sequence[Ref], *, batch_size: int = 100) -> Dict[Ref, Dict[str, Any]]` — режет на батчи и зовёт `api.wall.getById`
  - значения словаря: `{"views": Optional[int], "likes": Optional[int], "comments": Optional[int], "reposts": Optional[int], "published_at": Optional[datetime]}`

**Почему общий модуль, а не копия.** Батч-обёртка над `wall.getById` уже есть в `ad_cabinet/publication_stats.py`. Копировать её — тот самый класс, из-за которого D-024 сводил три копии `_call_api` в один `deepseek_client` («вторая и третья разошлись бы молча») и из-за которого VK-токены сводили к единому источнику. Общим делается разбор ответа и нарезка батчей; **политика выбора токена остаётся у каждого потребителя** — у кабинета она своя (user-token админа приоритетнее community).

- [ ] **Step 1: Написать падающий тест**

Создать `tests/test_vk_monitor/test_post_metrics.py`:

```python
"""Общий батч-фетчер метрик постов (wall.getById).

Единственное место, где парсится ответ ВК по метрикам. Тесты держат два
свойства: NULL не превращается в 0, и падение одного батча не уносит остальные.
"""

from datetime import datetime

from modules.vk_monitor.post_metrics import fetch_metrics_for_token, parse_metrics_items


def test_parse_reads_all_four_metrics_and_date():
    items = [
        {
            "owner_id": -100, "id": 7, "date": 1787136000,
            "views": {"count": 1224}, "likes": {"count": 7},
            "comments": {"count": 2}, "reposts": {"count": 1},
        }
    ]
    out = parse_metrics_items(items)
    assert out[(-100, 7)] == {
        "views": 1224, "likes": 7, "comments": 2, "reposts": 1,
        "published_at": datetime(2026, 8, 19, 10, 40),
    }


def test_missing_views_stays_none_not_zero():
    # 10% постов приезжают без поля views. Ноль тут соврал бы: рейтинг делит
    # на (views+1), и «ноль просмотров» подняло бы пост наверх.
    items = [{"owner_id": -1, "id": 2, "likes": {"count": 3}}]
    out = parse_metrics_items(items)
    assert out[(-1, 2)]["views"] is None
    assert out[(-1, 2)]["likes"] == 3
    assert out[(-1, 2)]["comments"] is None


def test_parse_skips_items_without_usable_ids():
    items = [{"likes": {"count": 1}}, {"owner_id": "х", "id": 1}]
    assert parse_metrics_items(items) == {}


def test_fetch_splits_into_batches_of_hundred():
    calls = []

    class FakeApi:
        class wall:
            @staticmethod
            def getById(posts):
                calls.append(posts.split(","))
                return []

    refs = [(-1, i) for i in range(250)]
    fetch_metrics_for_token(FakeApi, refs, batch_size=100)
    assert [len(c) for c in calls] == [100, 100, 50]


def test_one_failing_batch_does_not_lose_the_others():
    class FlakyApi:
        class wall:
            seen = 0

            @classmethod
            def getById(cls, posts):
                cls.seen += 1
                if cls.seen == 1:
                    raise RuntimeError("VK упал")
                return [{"owner_id": -1, "id": 200, "likes": {"count": 5}}]

    refs = [(-1, i) for i in range(150)]
    out = fetch_metrics_for_token(FlakyApi, refs, batch_size=100)
    assert (-1, 200) in out, "второй батч обязан отработать после падения первого"


def test_response_may_be_dict_with_items():
    class DictApi:
        class wall:
            @staticmethod
            def getById(posts):
                return {"items": [{"owner_id": -5, "id": 1, "likes": {"count": 2}}]}

    out = fetch_metrics_for_token(DictApi, [(-5, 1)])
    assert out[(-5, 1)]["likes"] == 2
```

- [ ] **Step 2: Прогнать тест, убедиться что падает**

Run: `./venv/Scripts/python.exe -m pytest tests/test_vk_monitor/test_post_metrics.py -q --basetemp=C:/Temp/claude/D--PROGRAMMING-setka/fb2d97a1-a6d0-4c47-8162-2df4d83cc462/scratchpad/pt -p no:cacheprovider`

Expected: FAIL, `ModuleNotFoundError: modules.vk_monitor.post_metrics`

- [ ] **Step 3: Написать модуль**

Создать `modules/vk_monitor/post_metrics.py`:

```python
"""Батч-чтение метрик постов ВК через ``wall.getById`` — единственное место.

**Почему модуль общий.** Такая обёртка уже была написана в
``modules/ad_cabinet/publication_stats.py`` под свои посты кабинета. Вторая
копия под чужие посты районов разошлась бы с первой молча — ровно тот класс
отказа, из-за которого D-024 сводил три копии ``_call_api`` в один
``deepseek_client``, а решение 2026-07-12 сводило VK-токены к единому
источнику. Общим здесь делается разбор ответа и нарезка батчей.

**Политика выбора токена сюда НЕ переезжает.** У кабинета она своя
(user-token админа видит просмотры своих постов, иначе community-token), у
обновления метрик района — своя (живой READ-токен из роутера). Общий модуль
получает уже готовый ``api``-объект.

**``None`` ≠ ``0``.** Поля, которых ВК не прислал, остаются ``None``: рейтинг
делит на ``(views + 1)``, и «ноль просмотров» вместо «не измеряли» подняло бы
такой пост в верхушку отбора.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, Optional, Sequence, Tuple

from utils.post_utils import vk_post_datetime

logger = logging.getLogger(__name__)

Ref = Tuple[int, int]  # (owner_id со знаком, post_id)

BATCH_SIZE = 100  # потолок wall.getById


def _count(item: Dict[str, Any], key: str) -> Optional[int]:
    """``{"count": N}`` → N; поля нет → ``None`` (не ноль)."""
    block = item.get(key)
    if not isinstance(block, dict) or "count" not in block:
        return None
    try:
        return int(block["count"])
    except (TypeError, ValueError):
        return None


def parse_metrics_items(items: Iterable[Dict[str, Any]]) -> Dict[Ref, Dict[str, Any]]:
    """Разбор ответа ``wall.getById`` в словарь по ``(owner_id, post_id)``.

    Чистая функция без сети — весь разбор тестируется без ВК.
    """
    out: Dict[Ref, Dict[str, Any]] = {}
    for item in items or []:
        if not isinstance(item, dict):
            continue
        try:
            key: Ref = (int(item["owner_id"]), int(item["id"]))
        except (KeyError, TypeError, ValueError):
            continue
        out[key] = {
            "views": _count(item, "views"),
            "likes": _count(item, "likes"),
            "comments": _count(item, "comments"),
            "reposts": _count(item, "reposts"),
            "published_at": vk_post_datetime(item.get("date")),
        }
    return out


def fetch_metrics_for_token(
    api: Any,
    refs: Sequence[Ref],
    *,
    batch_size: int = BATCH_SIZE,
) -> Dict[Ref, Dict[str, Any]]:
    """Метрики для списка постов одним токеном, батчами по ``batch_size``.

    ``api`` — уже собранный объект ``vk_api.VkApi(token=...).get_api()``.

    Падение отдельного батча логируется и пропускается: обновление метрик —
    фоновая работа, и один отказ ВК не должен стоить остальных семи тысяч
    постов. Пропущенные остаются с прежними значениями (в том числе ``NULL``).
    """
    out: Dict[Ref, Dict[str, Any]] = {}
    for i in range(0, len(refs), batch_size):
        chunk = refs[i : i + batch_size]
        posts_str = ",".join(f"{o}_{p}" for o, p in chunk)
        try:
            resp = api.wall.getById(posts=posts_str)
        except Exception as e:  # pragma: no cover - сеть
            logger.warning("wall.getById batch failed (%d posts): %s", len(chunk), e)
            continue
        items = resp if isinstance(resp, list) else (resp or {}).get("items") or []
        out.update(parse_metrics_items(items))
    return out
```

- [ ] **Step 4: Прогнать тест, убедиться что проходит**

Run: `./venv/Scripts/python.exe -m pytest tests/test_vk_monitor/test_post_metrics.py -q --basetemp=C:/Temp/claude/D--PROGRAMMING-setka/fb2d97a1-a6d0-4c47-8162-2df4d83cc462/scratchpad/pt -p no:cacheprovider`

Expected: PASS, 6 тестов

- [ ] **Step 5: Перевести кабинет на общий фетчер**

В `modules/ad_cabinet/publication_stats.py` заменить тело внутреннего цикла `_build_default_fetcher` — вместо своей нарезки и своего разбора звать общий модуль. Заменить блок со строки `out: Dict[Ref, Dict[str, int]] = {}` до `return out` включительно на:

```python
        from modules.vk_monitor.post_metrics import fetch_metrics_for_token

        out: Dict[Ref, Dict[str, Any]] = {}
        for token, grp in by_token.items():
            api = vk_api.VkApi(token=token).get_api()
            # Разбор и нарезка — в общем модуле (см. его docstring: вторая копия
            # разошлась бы с первой молча). Своей здесь остаётся только политика
            # токенов выше: user-token админа видит просмотры, community — нет.
            out.update(fetch_metrics_for_token(api, grp))
        return out
```

Поправить импорт типов в шапке файла на `from typing import Any, Dict, List, Tuple`.

- [ ] **Step 6: Прогнать тесты кабинета, убедиться что не сломались**

Run: `./venv/Scripts/python.exe -m pytest tests/ -q -k "ad_cabinet or publication_stats" --basetemp=C:/Temp/claude/D--PROGRAMMING-setka/fb2d97a1-a6d0-4c47-8162-2df4d83cc462/scratchpad/pt -p no:cacheprovider`

Expected: PASS без правок

Проверено при написании плана: `tests/test_ad_cabinet/test_publication_stats.py` проверяет поля модели (`pub.views`, `pub.likes`, `pub.reposts`), а не ключи словаря фетчера. Два новых ключа (`comments`, `published_at`) в возвращаемом словаре — расширение, которого эти тесты не касаются. Если они всё же покраснели — значит фетчер подменили не там, где надо: смотреть на политику токенов, её трогать было нельзя.

- [ ] **Step 7: Коммит**

```bash
git add modules/vk_monitor/post_metrics.py modules/ad_cabinet/publication_stats.py tests/test_vk_monitor/test_post_metrics.py
git commit -m "refactor(vk): один батч-фетчер wall.getById вместо двух копий"
```

---

### Task 5: Таска обновления метрик

**Files:**
- Create: `modules/classifier/metrics_refresh.py`
- Modify: `tasks/celery_app.py` (таска + запись в `beat_schedule`)
- Test: `tests/test_classifier/test_metrics_refresh.py` (create)

**Interfaces:**
- Consumes: `fetch_metrics_for_token`, `Ref` (Task 4); `CollectedPostAudit.published_at/.views/...` (Task 2)
- Produces:
  - `select_refresh_candidates(session, *, hours: int = 72, limit: int = 0) -> List[Tuple[Ref, str]]` — `((owner_id, post_id), lip)` для постов в окне
  - `drop_already_published(candidates, published_lips: Set[str]) -> List[Tuple[Ref, str]]` — чистая
  - `refresh_metrics(session, *, hours: int = 72) -> Dict[str, Any]` — весь проход, возвращает `{"ok": bool, "checked": int, "updated": int, "skipped_published": int}`
  - Celery-таска `tasks.celery_app.refresh_post_metrics`

**Окно 72 часа считается по `published_at`**, а для строк, где она ещё `NULL` (существующие 7774) — запасным критерием по `collected_at`. Это одноразовая ситуация: таска сама заполнит дату из ответа ВК.

- [ ] **Step 1: Написать падающий тест**

Создать `tests/test_classifier/test_metrics_refresh.py`:

```python
"""Обновление метрик постов в окне 72 часов (звено 5, шаг 1).

Границы отбора проверяются на чистых функциях: «старше 72 часов не трогаем»
и «уже опубликованное нами не трогаем» — это правила владельца, и они должны
падать тестом, а не выясняться на счёте вызовов ВК.
"""

from modules.classifier.metrics_refresh import drop_already_published, ref_from_post_url


def test_ref_from_post_url_keeps_owner_sign():
    # lip теряет знак owner_id (abs), а wall.getById его требует. Знак
    # восстанавливаем из post_url, где он сохранён.
    assert ref_from_post_url("https://vk.com/wall-196153274_8272") == (-196153274, 8272)


def test_ref_from_broken_url_is_none():
    for bad in ("", None, "https://vk.com/id1", "https://vk.com/wallабв_1"):
        assert ref_from_post_url(bad) is None, f"url={bad!r}"


def test_drop_already_published_removes_ours_only():
    cands = [((-1, 10), "1_10"), ((-2, 20), "2_20"), ((-3, 30), "3_30")]
    out = drop_already_published(cands, {"2_20"})
    assert [lip for _, lip in out] == ["1_10", "3_30"]


def test_drop_already_published_with_empty_set_keeps_everything():
    cands = [((-1, 10), "1_10")]
    assert drop_already_published(cands, set()) == cands
```

- [ ] **Step 2: Прогнать тест, убедиться что падает**

Run: `./venv/Scripts/python.exe -m pytest tests/test_classifier/test_metrics_refresh.py -q --basetemp=C:/Temp/claude/D--PROGRAMMING-setka/fb2d97a1-a6d0-4c47-8162-2df4d83cc462/scratchpad/pt -p no:cacheprovider`

Expected: FAIL, `ModuleNotFoundError: modules.classifier.metrics_refresh`

- [ ] **Step 3: Написать модуль**

Создать `modules/classifier/metrics_refresh.py`:

```python
"""Обновление метрик собранных постов — данные под рейтинг (звено 5, шаг 1).

Метрики в момент сбора почти нулевые: пост собирается через минуты после
публикации, и лайков у него ещё нет. Поэтому рейтинг строится не на том, что
видел сбор, а на том, что доросло за окно отсева.

**Границы прохода — правила владельца, не оптимизация:**

* **не трогаем посты старше 72 часов** — они всё равно отсеются по старости,
  и тратить на них вызовы ВК незачем;
* **не трогаем уже опубликованное нами** (``work_tables.lip``) — их рейтинг
  ни на что не влияет, пост из мешка уже ушёл;
* **берём обе стороны аудита, ``kept`` и ``dropped``.** Без метрик на
  отсеянных нельзя проверить находку D-024 (ИИ считает публикуемыми 43% того,
  что выкинули алгоритмы), а именно на неё опирается будущее снятие фильтров.

Объём посчитан на проде 2026-08-19: окно 72 часа = 7774 строки по 29 регионам,
то есть 78 батчей за круг и ~620 вызовов в сутки при прогоне раз в 3 часа.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

logger = logging.getLogger(__name__)

Ref = Tuple[int, int]

_WALL_RE = re.compile(r"wall(-?\d+)_(\d+)\s*$")

REFRESH_WINDOW_HOURS = 72


def ref_from_post_url(url: Optional[str]) -> Optional[Ref]:
    """``https://vk.com/wall-100_7`` → ``(-100, 7)``.

    ``lip`` для этого не годится: он хранится как ``{abs(owner_id)}_{id}`` и
    знак владельца теряет, а ``wall.getById`` без знака отдаст чужой пост или
    ничего. В ``post_url`` знак сохранён — берём оттуда.
    """
    if not url:
        return None
    m = _WALL_RE.search(str(url))
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)))


def drop_already_published(
    candidates: Sequence[Tuple[Ref, str]],
    published_lips: Set[str],
) -> List[Tuple[Ref, str]]:
    """Выкинуть посты, которые мы уже опубликовали. Чистая функция."""
    if not published_lips:
        return list(candidates)
    return [(ref, lip) for ref, lip in candidates if lip not in published_lips]


async def load_published_lips(session) -> Set[str]:
    """Все lip'ы, опубликованные нами, из ``work_tables.lip`` (JSON-списки)."""
    from sqlalchemy import select

    from database.models_extended import WorkTable

    out: Set[str] = set()
    rows = (await session.execute(select(WorkTable.lip))).all()
    for (lips,) in rows:
        for lip in lips or []:
            out.add(str(lip))
    return out


async def select_refresh_candidates(
    session,
    *,
    hours: int = REFRESH_WINDOW_HOURS,
    limit: int = 0,
) -> List[Tuple[Ref, str]]:
    """Посты аудита в окне ``hours``, пригодные для обновления метрик.

    Окно считается по ``published_at`` (возраст поста). Строки, где она ещё
    ``NULL`` — наследие до миграции 080 — добираются по ``collected_at``:
    ситуация одноразовая, таска сама проставит дату из ответа ВК.
    """
    from sqlalchemy import or_, select

    from database.models_extended import CollectedPostAudit

    cutoff = datetime.utcnow() - timedelta(hours=hours)
    stmt = (
        select(CollectedPostAudit.post_url, CollectedPostAudit.lip)
        .where(
            or_(
                CollectedPostAudit.published_at > cutoff,
                (CollectedPostAudit.published_at.is_(None))
                & (CollectedPostAudit.collected_at > cutoff),
            )
        )
        .order_by(CollectedPostAudit.collected_at.desc())
    )
    if limit:
        stmt = stmt.limit(limit)

    out: List[Tuple[Ref, str]] = []
    for url, lip in (await session.execute(stmt)).all():
        ref = ref_from_post_url(url)
        if ref is not None:
            out.append((ref, lip))
    return out


async def apply_metrics(session, metrics_by_ref: Dict[Ref, Dict[str, Any]],
                        lip_by_ref: Dict[Ref, str]) -> int:
    """Записать метрики в аудит. Возвращает число обновлённых строк.

    ``published_at`` перезаписывается только когда его ещё нет: дата поста не
    меняется, а ответ ВК может её и не принести.
    """
    from sqlalchemy import update

    from database.models_extended import CollectedPostAudit

    now = datetime.utcnow()
    updated = 0
    for ref, m in metrics_by_ref.items():
        lip = lip_by_ref.get(ref)
        if not lip:
            continue
        values: Dict[str, Any] = {
            "views": m.get("views"),
            "likes": m.get("likes"),
            "comments": m.get("comments"),
            "reposts": m.get("reposts"),
            "metrics_updated_at": now,
        }
        stmt = update(CollectedPostAudit).where(CollectedPostAudit.lip == lip)
        if m.get("published_at"):
            # Только если даты ещё нет — она не меняется со временем.
            await session.execute(
                stmt.where(CollectedPostAudit.published_at.is_(None)).values(
                    published_at=m["published_at"], **values
                )
            )
            await session.execute(
                stmt.where(CollectedPostAudit.published_at.isnot(None)).values(**values)
            )
        else:
            await session.execute(stmt.values(**values))
        updated += 1
    await session.commit()
    return updated


async def refresh_metrics(session, *, hours: int = REFRESH_WINDOW_HOURS) -> Dict[str, Any]:
    """Один круг обновления метрик. Никогда не бросает наружу."""
    import vk_api

    from modules.vk_monitor.post_metrics import fetch_metrics_for_token
    from modules.vk_token_router import get_healthy_read_token

    candidates = await select_refresh_candidates(session, hours=hours)
    published = await load_published_lips(session)
    live = drop_already_published(candidates, published)
    skipped = len(candidates) - len(live)
    if not live:
        return {"ok": True, "checked": 0, "updated": 0, "skipped_published": skipped}

    token = await get_healthy_read_token()
    if not token:
        # Молчать нельзя: без токена метрики не обновятся ни разу, а рейтинг
        # тихо застынет на старых числах.
        logger.warning("refresh_metrics: живого READ-токена нет, круг пропущен")
        return {"ok": False, "error": "no_read_token", "checked": len(live), "updated": 0,
                "skipped_published": skipped}

    api = vk_api.VkApi(token=token).get_api()
    lip_by_ref = {ref: lip for ref, lip in live}
    metrics = fetch_metrics_for_token(api, [ref for ref, _ in live])
    updated = await apply_metrics(session, metrics, lip_by_ref)
    return {"ok": True, "checked": len(live), "updated": updated,
            "skipped_published": skipped}
```

- [ ] **Step 4: Прогнать тест, убедиться что проходит**

Run: `./venv/Scripts/python.exe -m pytest tests/test_classifier/test_metrics_refresh.py -q --basetemp=C:/Temp/claude/D--PROGRAMMING-setka/fb2d97a1-a6d0-4c47-8162-2df4d83cc462/scratchpad/pt -p no:cacheprovider`

Expected: PASS, 4 теста

- [ ] **Step 5: Добавить Celery-таску и beat**

В `tasks/celery_app.py` рядом с `classify_pending_posts` добавить:

```python
@app.task(name="tasks.celery_app.refresh_post_metrics")
def refresh_post_metrics(hours: int = 0):
    """Обновление метрик собранных постов — данные под рейтинг (звено 5, шаг 1).

    Метрики в момент сбора почти нулевые (пост собран через минуты после
    публикации). Рейтинг строится на том, что доросло за окно отсева, поэтому
    цифры надо догонять фоном.
    """
    try:
        from modules.classifier.metrics_refresh import REFRESH_WINDOW_HOURS, refresh_metrics

        async def _run():
            from database.connection import AsyncSessionLocal

            async with AsyncSessionLocal() as session:
                return await refresh_metrics(session, hours=hours or REFRESH_WINDOW_HOURS)

        # run_coro, а не asyncio.run: в prefork-воркере петля переиспользуется
        # процессом (utils/celery_asyncio) — общая идиома всех async-тасок здесь.
        return run_coro(_run())
    except Exception as e:
        # Обновление метрик — вспомогательная работа: её отказ не должен
        # ронять beat-цепочку. Но и молчать нельзя, иначе рейтинг тихо
        # застынет на старых числах (тот же класс, что инцидент 19.08,
        # где таска рапортовала успех, ничего не сделав).
        logger.error("refresh_post_metrics failed: %s", e, exc_info=True)
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
```

`run_coro` уже импортирован в `tasks/celery_app.py` — это идиома всех async-тасок файла (см. `classify_pending_posts`, строка ~685). Отдельного импорта не требуется.

В `beat_schedule` добавить запись рядом с `classifier-headless`:

```python
    # Метрики постов под рейтинг отбора (звено 5, шаг 1): каждые 3 часа на :05.
    # Сдвиг от classifier-headless (:35) — сознательный: обе таски ходят за
    # живым READ-токеном, и толкаться за ним в одну минуту незачем.
    # Объём круга посчитан на проде 19.08: окно 72 часа = 7774 поста = 78
    # батчей wall.getById, ~620 вызовов в сутки.
    "post-metrics-refresh": {
        "task": "tasks.celery_app.refresh_post_metrics",
        "schedule": crontab(minute=5, hour="*/3"),
        "options": {"expires": 3 * 3600, "catchup": False},
    },
```

- [ ] **Step 6: Прогнать весь набор тестов**

Run: `./venv/Scripts/python.exe -m pytest tests/ -q --basetemp=C:/Temp/claude/D--PROGRAMMING-setka/fb2d97a1-a6d0-4c47-8162-2df4d83cc462/scratchpad/pt -p no:cacheprovider`

Expected: PASS, регрессий нет

- [ ] **Step 7: Коммит**

```bash
git add modules/classifier/metrics_refresh.py tasks/celery_app.py tests/test_classifier/test_metrics_refresh.py
git commit -m "feat(classifier): таска обновления метрик постов в окне 72 часов"
```

---

### Task 6: Витрина топ-N — сервис и эндпоинт

**Files:**
- Create: `modules/classifier/rating.py`
- Modify: `web/api/classifier_review.py`
- Test: `tests/test_classifier/test_rating_top.py` (create)

**Interfaces:**
- Consumes: `post_rating` (Task 1), `get_rating_views_alpha` (Task 1), `selection.fetch_publish_lips` (существует)
- Produces:
  - `rank_rows(rows: Sequence[Dict[str, Any]], *, alpha: float, n: int) -> List[Dict[str, Any]]` — чистая; добавляет ключ `score` и сортирует
  - `top_by_rating(session, *, region_code: str, theme: Optional[str], n: int, alphas: Optional[Sequence[float]] = None) -> Dict[str, Any]` — при `alphas=None` первой идёт настроенная `RATING_VIEWS_ALPHA`, затем опорные 0.5 и 0.0 (дубли схлопываются)
  - `GET /api/classifier-review/rating/top?region=&theme=&n=`

**Маршрут ставится ВЫШЕ параметризованных путей** — как `/bulk/*` после инцидента 2026-08-19. Гейт на затенение в `tests/test_classifier/test_review_api_routes.py` это проверяет автоматически.

- [ ] **Step 1: Написать падающий тест**

Создать `tests/test_classifier/test_rating_top.py`:

```python
"""Витрина топ-N по рейтингу (звено 5, шаг 1) — ранжирование и HTTP.

HTTP-слой проверяется отдельно и намеренно: инцидент 2026-08-19 показал, что
групповые ручки этого же роутера были покрыты на уровне сервиса и при этом
недостижимы снаружи (статический путь затенялся параметризованным).
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from modules.classifier.rating import rank_rows
from web.api import classifier_review as rev


def _row(lip, views, likes=0, comments=0, reposts=0):
    return {"lip": lip, "views": views, "likes": likes,
            "comments": comments, "reposts": reposts}


def test_rank_sorts_by_score_descending():
    rows = [_row("a", 10000, 100), _row("b", 20, 12)]
    out = rank_rows(rows, alpha=0.25, n=10)
    assert [r["lip"] for r in out] == ["a", "b"]
    out_half = rank_rows(rows, alpha=0.5, n=10)
    assert [r["lip"] for r in out_half] == ["b", "a"]


def test_rank_puts_unmeasured_views_last():
    # score=None — «не мерили». Такой пост не должен обгонять измеренные,
    # каким бы ни было число лайков.
    rows = [_row("no-views", None, 999), _row("measured", 100, 1)]
    out = rank_rows(rows, alpha=0.25, n=10)
    assert [r["lip"] for r in out] == ["measured", "no-views"]
    assert out[-1]["score"] is None


def test_rank_respects_n():
    rows = [_row(str(i), 100, i) for i in range(20)]
    assert len(rank_rows(rows, alpha=0.25, n=5)) == 5


def test_rank_on_empty_input_is_empty():
    assert rank_rows([], alpha=0.25, n=10) == []


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(rev.router, prefix="/api/classifier-review")
    return TestClient(app)


def test_endpoint_requires_region(client):
    # Рейтинги районов между собой несопоставимы — общий топ был бы бессмыслен.
    r = client.get("/api/classifier-review/rating/top")
    assert r.status_code == 422, r.text


def test_endpoint_reaches_the_service_and_not_the_id_route(client):
    fake = AsyncMock(return_value={"region": "mi", "alphas": {}, "rows": []})
    with patch.object(rev.rating, "top_by_rating", fake):
        r = client.get("/api/classifier-review/rating/top?region=mi&n=5")
    assert r.status_code == 200, r.text
    assert fake.await_count == 1
    assert fake.await_args.kwargs["region_code"] == "mi"
    assert fake.await_args.kwargs["n"] == 5
```

- [ ] **Step 2: Прогнать тест, убедиться что падает**

Run: `./venv/Scripts/python.exe -m pytest tests/test_classifier/test_rating_top.py -q --basetemp=C:/Temp/claude/D--PROGRAMMING-setka/fb2d97a1-a6d0-4c47-8162-2df4d83cc462/scratchpad/pt -p no:cacheprovider`

Expected: FAIL, `ModuleNotFoundError: modules.classifier.rating`

- [ ] **Step 3: Написать модуль ранжирования**

Создать `modules/classifier/rating.py`:

```python
"""Витрина топ-N по рейтингу — измерение, а не отбор (звено 5, шаг 1).

**Ничего не публикует и ни на что не влияет.** Задача одна: показать
владельцу, как выглядит верхушка при разных значениях ``alpha``, чтобы он
выбрал одно глазами на боевых постах. Переключение публикации на рейтинг —
следующий заход, и до него порядок жёсткий: рейтинг ДО снятия фильтров.

Топ считается ВНУТРИ разрешённых вердиктом (``selection.fetch_publish_lips``)
— иначе витрина показывала бы то, что нейро-фильтр уже отверг.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence

from utils.post_utils import post_rating

logger = logging.getLogger(__name__)

WINDOW_HOURS = 72


def rank_rows(rows: Sequence[Dict[str, Any]], *, alpha: float, n: int) -> List[Dict[str, Any]]:
    """Проставить ``score`` и вернуть верхушку из ``n`` строк.

    Посты без измеренных просмотров (``score is None``) уходят в хвост, а не
    наверх: их балл неизвестен, и делать вид, что он нулевой или высокий,
    одинаково неправда.
    """
    scored = []
    for row in rows:
        item = dict(row)
        item["score"] = post_rating(
            row.get("views"), row.get("likes"), row.get("comments"), row.get("reposts"),
            alpha=alpha,
        )
        scored.append(item)
    scored.sort(key=lambda r: (r["score"] is not None, r["score"] or 0.0), reverse=True)
    return scored[:n]


async def top_by_rating(
    session,
    *,
    region_code: str,
    theme: Optional[str] = None,
    n: int = 10,
    alphas: Optional[Sequence[float]] = None,
) -> Dict[str, Any]:
    """Верхушка рейтинга района по каждому из ``alphas``.

    По умолчанию первой идёт НАСТРОЕННАЯ alpha (``RATING_VIEWS_ALPHA``), а не
    жёсткая 0.25: витрина существует ради выбора этого значения и обязана
    показывать то, что реально стоит в конфиге. Рядом — две опорные точки:
    0.5 (как сортируется лента сейчас) и 0 (чистый охват).

    Окно — те же 72 часа, что у отбора: показывать надо то, из чего реально
    можно выбрать.
    """
    from sqlalchemy import or_, select

    from config.classifier import get_rating_views_alpha
    from database.models_extended import CollectedPostAudit
    from modules.classifier import selection

    if alphas is None:
        configured = get_rating_views_alpha()
        # dict.fromkeys, а не set: порядок колонок на витрине задан, а
        # настроенная alpha может совпасть с опорной — дубля быть не должно.
        alphas = list(dict.fromkeys([configured, 0.5, 0.0]))

    cutoff = datetime.utcnow() - timedelta(hours=WINDOW_HOURS)
    stmt = select(
        CollectedPostAudit.lip,
        CollectedPostAudit.post_url,
        CollectedPostAudit.post_text,
        CollectedPostAudit.theme,
        CollectedPostAudit.views,
        CollectedPostAudit.likes,
        CollectedPostAudit.comments,
        CollectedPostAudit.reposts,
        CollectedPostAudit.published_at,
        CollectedPostAudit.metrics_updated_at,
    ).where(
        CollectedPostAudit.region_code == region_code,
        or_(
            CollectedPostAudit.published_at > cutoff,
            (CollectedPostAudit.published_at.is_(None))
            & (CollectedPostAudit.collected_at > cutoff),
        ),
    )
    if theme:
        stmt = stmt.where(CollectedPostAudit.theme == theme)

    allowed = await selection.fetch_publish_lips(session, region_code)
    rows: List[Dict[str, Any]] = []
    for r in (await session.execute(stmt)).all():
        if r.lip not in allowed:
            continue
        rows.append(
            {
                "lip": r.lip,
                "post_url": r.post_url,
                "post_text": (r.post_text or "")[:300],
                "theme": r.theme,
                "views": r.views,
                "likes": r.likes,
                "comments": r.comments,
                "reposts": r.reposts,
                "published_at": r.published_at.isoformat() if r.published_at else None,
                "metrics_updated_at": (
                    r.metrics_updated_at.isoformat() if r.metrics_updated_at else None
                ),
            }
        )

    measured = sum(1 for r in rows if r["views"] is not None)
    return {
        "region": region_code,
        "theme": theme or "",
        "candidates": len(rows),
        # Охват источника рядом с числом — правило после #493: у каждой цифры
        # на панели спрашивать, какую долю реальности покрывает её источник.
        "measured": measured,
        "alphas": {str(a): rank_rows(rows, alpha=a, n=n) for a in alphas},
    }
```

- [ ] **Step 4: Добавить эндпоинт**

В `web/api/classifier_review.py`:

1. В импортах заменить `from modules.classifier import rules, service` на `from modules.classifier import rating, rules, service`.
2. Добавить маршрут **в блок групповых действий, выше параметризованных путей** (тот, что начинается комментарием «Групповые действия»):

```python
@router.get("/rating/top")
async def rating_top(
    region: str = Query(..., min_length=1, description="код района (обязателен)"),
    theme: str = Query("", description="тема (опционально)"),
    n: int = Query(10, ge=1, le=100, description="сколько строк в топе"),
):
    """Витрина топ-N по рейтингу — измерение, публикацию не трогает.

    ``region`` обязателен намеренно: рейтинги районов между собой
    несопоставимы (у больших пабликов свои порядки просмотров), и общий топ
    по сети был бы числом без смысла.
    """
    async with AsyncSessionLocal() as session:
        return await rating.top_by_rating(
            session, region_code=region.strip(), theme=theme.strip() or None, n=n
        )
```

- [ ] **Step 5: Прогнать тест, убедиться что проходит**

Run: `./venv/Scripts/python.exe -m pytest tests/test_classifier/test_rating_top.py tests/test_classifier/test_review_api_routes.py -q --basetemp=C:/Temp/claude/D--PROGRAMMING-setka/fb2d97a1-a6d0-4c47-8162-2df4d83cc462/scratchpad/pt -p no:cacheprovider`

Expected: PASS — в том числе гейт на затенение маршрутов

- [ ] **Step 6: Коммит**

```bash
git add modules/classifier/rating.py web/api/classifier_review.py tests/test_classifier/test_rating_top.py
git commit -m "feat(classifier): витрина топ-N по рейтингу для подбора alpha"
```

---

### Task 7: Витрина на странице `/classifier`

**Files:**
- Modify: `web/templates/classifier.html`
- Test: ручная проверка + `node --check` на вырезанном `<script>`

**Interfaces:**
- Consumes: `GET /api/classifier-review/rating/top` (Task 6)

- [ ] **Step 1: Добавить блок разметки**

В `web/templates/classifier.html` вставить карточку сразу после карточки «Здоровье фильтра»:

```html
<div class="card mb-3">
  <div class="card-header d-flex justify-content-between align-items-center flex-wrap gap-2 py-2">
    <div>
      <b>Витрина рейтинга</b>
      <span class="text-muted small">— измерение, публикацию не меняет</span>
    </div>
    <div class="d-flex gap-2 align-items-center">
      <input id="cf-rating-region" class="form-control form-control-sm" style="width: 120px"
             placeholder="район (mi)" />
      <input id="cf-rating-n" class="form-control form-control-sm" style="width: 80px"
             type="number" min="1" max="100" value="10" />
      <button class="btn btn-outline-primary btn-sm" onclick="loadRatingTop()">Показать</button>
    </div>
  </div>
  <div class="card-body py-2">
    <div class="small text-muted mb-2">
      Одна и та же выборка при разных &alpha;. <b>&alpha;=0.5</b> — как сортируется лента
      сейчас (побеждает отклик в маленькой группе), <b>&alpha;=0</b> — чистый охват,
      <b>&alpha;=0.25</b> — предложенный дефолт. Выбирается глазами на боевых постах.
    </div>
    <div id="cf-rating-box" class="text-muted small">Введите район и нажмите «Показать».</div>
  </div>
</div>
```

- [ ] **Step 2: Добавить загрузку и отрисовку**

Дописать в `<script>` рядом с `loadStats`:

```javascript
// Витрина рейтинга (звено 5, шаг 1) — ИЗМЕРЕНИЕ, публикацию не трогает.
// Три колонки показывают одну и ту же выборку при разных alpha, чтобы
// значение выбиралось глазами на боевых постах, а не наугад в конфиге.
async function loadRatingTop() {
    const region = document.getElementById("cf-rating-region").value.trim();
    const n = document.getElementById("cf-rating-n").value || 10;
    const box = document.getElementById("cf-rating-box");
    if (!region) { box.textContent = "Выберите район."; return; }
    box.innerHTML = `<span class="spinner-border spinner-border-sm"></span> считаю…`;
    const r = await fetch(`/api/classifier-review/rating/top?region=${encodeURIComponent(region)}&n=${n}`);
    if (!r.ok) { box.innerHTML = `<span class="text-danger">Не получилось: ${r.status}</span>`; return; }
    const d = await r.json();
    box.innerHTML = ratingHTML(d);
}

function ratingHTML(d) {
    // Охват источника рядом с числом — правило после #493: цифра без охвата
    // обещает больше, чем меряет.
    const head = `<div class="small text-muted mb-2">Кандидатов в окне 72 ч: <b>${d.candidates}</b>`
        + ` · с измеренными просмотрами: <b>${d.measured}</b></div>`;
    const col = (alpha, rows) => `
      <div class="col-md-4">
        <div class="fw-bold mb-1">α = ${alpha}</div>
        <ol class="small ps-3">${rows.map(r => `
          <li>
            <a href="${esc(r.post_url)}" target="_blank" rel="noopener">${esc(r.lip)}</a>
            <span class="badge bg-light text-dark">${r.score === null ? "не мерено" : r.score.toFixed(2)}</span>
            <div class="text-muted">👁 ${r.views ?? "—"} · ❤ ${r.likes ?? "—"} · 💬 ${r.comments ?? "—"} · ↗ ${r.reposts ?? "—"}</div>
          </li>`).join("")}</ol>
      </div>`;
    return head + `<div class="row">`
        + Object.entries(d.alphas).map(([a, rows]) => col(a, rows)).join("")
        + `</div>`;
}
```

- [ ] **Step 3: Проверить синтаксис JS**

Ни pytest, ни pre-commit синтаксическую ошибку в шаблоне не ловят — страница просто молча перестаёт работать. Вырезать содержимое `<script>` во временный файл (заменив вставки `{{ ... }}` на литерал) и прогнать:

Run: `node --check <scratchpad>/cf.js`
Expected: без вывода (синтаксис чист)

- [ ] **Step 4: Прогнать весь набор тестов**

Run: `./venv/Scripts/python.exe -m pytest tests/ -q --basetemp=C:/Temp/claude/D--PROGRAMMING-setka/fb2d97a1-a6d0-4c47-8162-2df4d83cc462/scratchpad/pt -p no:cacheprovider`

Expected: PASS

- [ ] **Step 5: Коммит**

```bash
git add web/templates/classifier.html
git commit -m "feat(classifier): блок витрины рейтинга на странице оператора"
```

---

## Выкатка и приёмка

Выполняется через `/reliz` после того, как все семь задач зелёные.

- [ ] **Миграция.** `/reliz` спросит через `AskUserQuestion` — применить `080_post_metrics.sql`. `migrate.py up` не использовать.
- [ ] **Restart** web и celery (новая таска и beat-запись подхватываются только рестартом).
- [ ] **Первый круг метрик вручную**, не дожидаясь beat: убедиться, что `checked` и `updated` ненулевые, а `skipped_published` осмыслен.
- [ ] **Проверка на живых данных:** доля строк окна с `views IS NOT NULL` после первого круга. Ожидание из пробы — около 90%; заметно ниже значит, что таска до части постов не дошла, и это надо разобрать до выбора `alpha`.
- [ ] **Показать владельцу витрину** на 2-3 районах и выбрать `alpha`. До этого выбора переключение отбора (шаг 2) не начинать.
- [ ] **Записать в PENDING** результат замера и выбранное значение.

## Что этот план сознательно НЕ делает

- Не переключает публикацию на отбор по рейтингу — это шаг 2, после выбора `alpha`.
- Не снимает редакционные фильтры — шаг 3; `hard_spam` не снимается вместе с остальными (fail-open нейро-фильтра + бан аккаунта за скам, G151).
- Не меняет веса `лайк/коммент/репост`, хотя проба показала, что на этих данных они почти не работают: менять два параметра сразу значит не узнать, который сработал.
