# Регионы — иерархия и каскадные дайджесты

Краткий референс по структуре регионов SETKA. Полная картина проекта — в [AI_DEV_GUIDE.md](AI_DEV_GUIDE.md).

---

## Три типа региона

| Тип | Что это | Пример (код) | Главное сообщество (`region.vk_group_id`) | Источники для своего дайджеста |
|---|---|---|---|---|
| **`raion`** | район — низший уровень | `mi`, `vp`, `nolinsk` | МАЛМЫЖ - ИНФО | записи в `communities` (партнёрские VK-паблики района) |
| **`oblast`** | область — содержит районы | `kirov_obl` | КИРОВСКАЯ ОБЛАСТЬ - ИНФО | главные сообщества подчинённых районов (`parent_region_id = oblast.id`) |
| **`strana`** | страна — верх иерархии | `rf` (когда создадим) | РОССИЯ - ИНФО | главные сообщества подчинённых областей |

**Иерархия:** `strana → oblast → raion`.

* Каждый `raion` принадлежит ровно одной `oblast` (через `parent_region_id`).
* Каждая `oblast` — ровно одной `strana` (через `parent_region_id`).
* Поле `Region.kind` (миграция 015): `raion` | `oblast` | `strana`. Default — `raion` (backward-compat для всех существующих записей).

---

## Словарь — фиксируем термины

* **главное сообщество региона** = `region.vk_group_id`. Туда публикуется дайджест. Иногда называется «ИНФО-страница» — это одно и то же. Хранится как отрицательный owner_id (например `-168170001` для `kirov_obl`).
* **источники региона**:
  * для `raion` = записи в `communities` где `region_id = raion.id` и `is_active=True`.
  * для `oblast` / `strana` = главные сообщества всех активных детей (`SELECT vk_group_id FROM regions WHERE parent_region_id = region.id AND is_active AND vk_group_id IS NOT NULL`).
* **дети региона** = активные регионы с `parent_region_id = region.id`. Только у `oblast` и `strana`.
* **районный дайджест** = старая логика (`tasks.parsing_scheduler_tasks.parse_and_publish_theme` — парсинг сообществ-партнёров → агрегация → фильтрация → публикация). Не меняется.
* **каскадный дайджест** = универсальная логика для `oblast` и `strana`. Берёт **N=5** свежих постов с `vk_group_id` каждого ребёнка → фильтрует рекламу/религию/дубли → публикует сводку в свой `vk_group_id`. Код — `modules/cascaded_digest.py`.

---

## Как работает каскадный дайджест

Beat-таски `postopus-kirov-oblast-*` (`tasks/celery_app.py`) вызывают `parse_and_publish_theme(region_code="kirov_obl", theme="oblast")`. Special-case в `tasks/parsing_scheduler_tasks.py` ловит регионы с `kind in ('oblast','strana')` и делегирует в `modules.cascaded_digest.run_cascaded_digest`. Шаги:

1. Загружаем `region` из БД, проверяем `kind in ('oblast','strana')` и наличие `vk_group_id`.
2. Резолвим **детей**: либо явный override `RegionConfig.digest_filters.defaults.cascade_source_region_codes` (список кодов), либо все активные регионы с `parent_region_id = region.id`.
3. Для каждого ребёнка читаем **`cascade_posts_per_child`** свежих постов со стены `child.vk_group_id` (default `5`). Слишком старые (старше `cascade_lookback_hours`, default `72ч`) — отсекаем.
4. Прогоняем собранные посты через общий `AdvancedVKParser.filter_posts_list` — дубли, реклама, повторы по `lip`/`hash`.
5. Hard-exclude'им рекламу/addons/религию (маркеры в `_BANNED_DIGEST_MARKERS` и `_RELIGIOUS_MARKERS`).
6. Собираем дайджест через `DigestBuilder` и публикуем в `region.vk_group_id` через `VKPublisher.create_with_policy` (с авто-fallback по политике токенов).
7. Обновляем `WorkTable.lip` и `WorkTable.hash` — чтобы следующий выпуск не повторил эти посты.
8. Записываем метрику `setka_digest_published_total{region,topic,result}`.

---

## Параметры

В `RegionConfig.digest_filters.defaults` для регионов `oblast` / `strana`:

| Поле | Default | Диапазон | Что делает |
|---|---|---|---|
| `cascade_posts_per_child` | `5` | 1-50 | Сколько свежих постов брать с каждого ребёнка |
| `cascade_lookback_hours` | `72.0` | 1-168 | Максимальный возраст поста |
| `cascade_source_region_codes` | `[]` | список кодов | Явный override детей (если не пуст — игнорируется `parent_region_id`) |

Старые поля `oblast_source_region_codes` / `oblast_wall_posts_per_source` / `oblast_max_wall_refs` из `modules/kirov_oblast_digest.py` больше не используются (хрупкая «extract wall.refs» механика удалена). На проде их можно оставить в RegionConfig — игнорируются.

---

## Соседский обмен новостями (cross-region)

Помимо вертикали `strana → oblast → raion` есть **горизонтальный** обмен между равными регионами-соседями. Каждый регион репостит к себе важные новости с главных групп тех регионов, что отмечены его соседями.

* **Источник соседей** — поле `Region.neighbors` (запятая-список кодов). Задаётся галочками в UI добавления/редактирования региона (multi-select существующих регионов) — адреса групп вводить не нужно, берётся `vk_group_id` каждого соседа.
* **Гейт** — в кандидаты попадают только посты с хэштегом `#Новости` (по умолчанию; override через `region.config['neighbor_hashtag']`). Реклама/детсады/прочее без хэштега не репостятся.
* **Движок** — тот же `modules/cascaded_digest.run_cascaded_digest` с `source_mode="neighbors"`, `theme="neighbors"` (тонкая обёртка `run_neighbor_digest`). **Без дублирования**: тот же сбор/фильтр/дедуп/публикация, что у каскадного дайджеста. Старый `modules/publisher/neighbor_sharing.py` удалён.
* **Расписание** — beat `digest-share-neighbors-daily` (раз в сутки, 8:30) → `run_all_regions_neighbor_share` → `share_neighbor_news(region_code)` по всем регионам с непустым `neighbors`.
* **Не путать с темой `sosed`** — та парсит сообщества с `category="sosed"` *внутри* одного региона (тема контента), это не cross-region обмен.

---

## Создание нового региона в иерархии

### Новый район (под существующую область)

```sql
INSERT INTO regions (code, name, vk_group_id, kind, parent_region_id, is_active)
VALUES (
    'novyi_raion',
    'НОВЫЙ РАЙОН - ИНФО',
    -123456789,
    'raion',
    (SELECT id FROM regions WHERE code = 'kirov_obl'),
    TRUE
);
```

Затем — добавить partner-`communities` через UI `/regions/novyi_raion/discovery` или прямым `INSERT INTO communities`.

### Новая область

```sql
INSERT INTO regions (code, name, vk_group_id, kind, is_active)
VALUES ('tatarstan_obl', 'ТАТАРСТАН - ИНФО', -...., 'oblast', TRUE);

-- Привязать районы к ней
UPDATE regions
SET parent_region_id = (SELECT id FROM regions WHERE code = 'tatarstan_obl')
WHERE code IN ('bal', 'kukmor');
```

Beat-таска для новой oblast не создастся автоматически — нужно добавить запись в `tasks/celery_app.py` (по аналогии с `postopus-kirov-oblast-*`).

### Новая страна

```sql
INSERT INTO regions (code, name, vk_group_id, kind, is_active)
VALUES ('rf', 'РОССИЯ - ИНФО', -...., 'strana', TRUE);

UPDATE regions
SET parent_region_id = (SELECT id FROM regions WHERE code = 'rf')
WHERE kind = 'oblast';
```

---

## История

* **2026-05-27** — миграция 015: добавлены `regions.kind` + `regions.parent_region_id`, создана запись `kirov_obl`, привязаны 13 районов Кировской области. Старый `modules/kirov_oblast_digest.py` стал тонким wrapper'ом над универсальным `modules/cascaded_digest.py`. См. PR с этим документом.
