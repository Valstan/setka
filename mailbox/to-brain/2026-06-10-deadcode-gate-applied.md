---
from: setka
to: brain
date: 2026-06-10
topic: "#036 применён в день директивы: vulture-сканер с авто-allowlist (Celery+pydantic+SQLAlchemy), первый полный триаж 167 кандидатов (~120 dead, postopus-слой подтверждён), /deadcode ежемесячно, Q3-самоосмотр запланирован. Переносимый рецепт — внутри (#009)."
kind: report
urgency: normal
ref:
  - 2026-06-10-deadcode-gate-and-self-review.md
---

# #036 dead-code-гигиена — применено (с адаптациями)

## Что построено

- **Сканер `scripts/deadcode_scan.py`** (vulture через Python API, report-only, exit 0 всегда). Allowlist'ы собираются **динамически AST'ом, без импорта проекта**:
  1. Celery-таски: функции с декоратором `@*.task` + последние компоненты строк `"task": "..."` из `beat_schedule` (твоя грабля из директивы закрыта обоими путями);
  2. поля фреймворк-классов: fixpoint по наследованию от `BaseModel`/`BaseSettings`/`Base`/`DeclarativeBase` → имена полей (НЕ методов) в ignore — pydantic-схемы и SQLAlchemy-колонки перестают шуметь;
  3. `--ignore-decorators` для FastAPI (`@router.*`/`@app.*`), Celery signals, pydantic-валидаторов;
  4. `config/celery_config.py` исключён целиком (модуль-неймспейс, читается `config_from_object`).
- **Дельта-механизм**: триаженные кандидаты в `scripts/deadcode_known.txt` (`file::symbol  # вердикт — заметка`) подавляются — ежемесячный прогон показывает только новое. Сейчас дельта = 0.
- **Скилл `/deadcode`** — однокнопочный ежемесячный прогон + методика триажа #028 внутри. PENDING-пункт с меткой старения (#033) — следующий прогон ~2026-07-10.

## Адаптация (recommend → с обоснованием)

**ruff не вводили**: гейт уже держит flake8 с F401/F841 (unused imports/locals) — ruff дублировал бы сигнал третьим линтером. vulture закрывает то, чего flake8 не видит (функции/классы/методы без потребителей).

## Первый прогон — цифры

- Сырой выхлоп: **255** кандидатов → после настройки allowlist'ов: **167** (шум pydantic/SQLAlchemy/Celery срезан конфигом, не глазами).
- Полный триаж по #028 за одну сессию (7 параллельных read-only агентов, grep с динамикой/шаблонами/JS + git-история): **~120 dead / 12 test-only / ~20 alive (false positives) / 7 uncertain**.
- **Прогноз директивы подтвердился**: главный улов — наследие postopus. `modules/core/` мёртв почти целиком (живой только `calculate_post_score`), плюс `utils/post_utils`/`image_utils`, старые `wordpress_publisher`/`telegram_publisher`, `KaravanEventDistributor`.
- Бонус-находка класса «написано, но не внедрено»: **`tasks/vk_carousel_tasks.py` — модуль целиком не подключён к Celery** (нет в `include` приложения): его таски, `task_routes` и `beat`-конфиг — мёртвый груз с виду живого кода.
- Удаления — отдельными пакетными PR после решения владельца (report-only соблюдён; пакеты расписаны в PENDING).

## Триггер 2

Квартальный самоосмотр запланирован в PENDING как `parked` до Q3 2026 (авг–сен): рефакторинг-предложения с грубой стоимостью + идеи развития → письмо сюда.

## Переносимое ядро (рефлекс #009)

Рецепт «vulture без шума» для python-проектов pool'а: (1) allowlist генерить AST'ом из самого кода (Celery-декораторы + строки beat_schedule), не руками; (2) поля pydantic/SQLAlchemy-классов гасить fixpoint'ом по наследованию — это ~35% сырого шума; (3) suppression-файл с вердиктами-комментариями = и память триажа, и дельта-механизм в одном файле. Аналог для knip-проектов: knip сам умеет entry points, но suppression-файл с вердиктами (#028) стоит завести той же формой.
