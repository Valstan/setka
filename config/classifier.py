"""Конфиг HITL-классификатора контента (ADR-0003).

Этап B (решение владельца 2026-07-05): классификацию делает облачная рутина
через HTTP-интерфейс (``/api/classifier``), а не Claude API. Ключ рутины —
``CLASSIFIER_INGEST_KEY`` (X-API-Key на ingest-эндпоинтах, как VK-шлюз).

Env vars:
  CLASSIFIER_INGEST_KEY       # секрет для облачной рутины (X-API-Key)
  CLASSIFIER_DISABLED=0       # аварийный kill-switch (1/true/yes/on → выкл.)
  CLASSIFIER_REGION_CODES     # allowlist кодов регионов для shadow (CSV);
                              # пусто = все регионы. Обкатка — один район.
  CLASSIFIER_PENDING_MAX=40   # потолок постов в одном /pending-батче
  CLASSIFIER_SOURCE_DAYS=3    # окно свежести источника (сутки): /pending видит
                              # только посты, собранные за последние N дней
  CLASSIFIER_PREPUBLISH_ENABLED=0     # классифицировать кандидатов ВНУТРИ волны,
                                      # до первой публикации (см.
                                      # modules/classifier/prepublish.py); дефолт выкл
  CLASSIFIER_SELECTION_ENABLED=0      # отбор В сводку по меткам нейросети
                                      # (звено 5, шаг 2): в волне остаются только
                                      # publish-посты, при молчании фильтра —
                                      # политика деградации владельца (см.
                                      # modules/classifier/selection.py); дефолт выкл
  CLASSIFIER_RULE_STALE_DAYS=90       # aging: approved-правило старше порога без
                                      # подачи в постулаты → подсветка в панели
  CLASSIFIER_RULES_SNAPSHOT_PATH      # файл снапшота выученных правил (beat);
                                      # дефолт logs/classifier_learned_rules_snapshot.md
  RATING_VIEWS_ALPHA=0.25     # показатель степени при просмотрах в рейтинге
                              # отбора; 0.5 = нынешняя post_popularity, 0 = охват
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)

# Файл-корректировщик («классификационные постулаты», ADR-0003 §E) — в репо,
# версия = git. Подаётся в промпт классификатора (рутины/API).
POSTULATES_PATH = Path(__file__).resolve().parent / "classification_postulates.md"


def get_ingest_key() -> str:
    """Секрет облачной рутины (env ``CLASSIFIER_INGEST_KEY``). Пусто = ingest выключен."""
    return (os.getenv("CLASSIFIER_INGEST_KEY") or "").strip()


def classifier_disabled() -> bool:
    """Kill-switch классификатора (env ``CLASSIFIER_DISABLED``). Дефолт — включён."""
    return os.getenv("CLASSIFIER_DISABLED", "0").strip().lower() in ("1", "true", "yes", "on")


def get_region_allowlist() -> List[str]:
    """Коды регионов для shadow (env ``CLASSIFIER_REGION_CODES``, CSV).

    Пусто → пустой список → «все регионы» (интерпретируется вызывающим кодом
    как отсутствие фильтра). Обкатка — один район (решение владельца).
    """
    raw = os.getenv("CLASSIFIER_REGION_CODES", "") or ""
    return [c.strip() for c in raw.replace(";", ",").split(",") if c.strip()]


def get_pending_max() -> int:
    """Потолок постов в одном ``/pending``-батче (env ``CLASSIFIER_PENDING_MAX``)."""
    try:
        return max(1, min(200, int(os.getenv("CLASSIFIER_PENDING_MAX", "40"))))
    except ValueError:
        return 40


def get_source_days() -> int:
    """Окно свежести источника ``/pending`` в сутках (env ``CLASSIFIER_SOURCE_DAYS``).

    Классификатор видит только посты, собранные за последние N дней — чтобы в
    ленту не попадало старьё, которое в сводку уже не пойдёт. Считается по
    ``collected_at`` аудита сбора (прокси даты публикации: механически-старые
    посты фильтр отсекает ещё при сборе, поэтому собранное — свежее).
    Границы 1..30.

    **Дефолт 1, а не 3 (решение владельца 2026-08-19: «жить только свежим»).**
    Трое суток окна означали, что после любой остановки движка фоновая таска
    неделями доедала посты, которым в сводку уже нельзя по правилу 72 часов:
    после аварии 16-18.08 в окне копилось под 7000 постов при пропускной
    способности 1600 в сутки, и backlog на панели переставал что-либо значить.
    Свежее теперь размечается прямо в волне (``prepublish``), так что широкое
    окно кормит уже только мусорную корзину.
    """
    try:
        return max(1, min(30, int(os.getenv("CLASSIFIER_SOURCE_DAYS", "1"))))
    except ValueError:
        return 1


def get_rule_stale_days() -> int:
    """Порог aging выученного правила в сутках (env ``CLASSIFIER_RULE_STALE_DAYS``).

    approved-правило, не подававшееся в эффективные постулаты дольше порога
    (по ``last_effective_at``), подсвечивается в панели как кандидат на вывод
    (retire, ADR-0005 §Aging / pool #033). Границы 7..365, дефолт 90.
    """
    try:
        return max(7, min(365, int(os.getenv("CLASSIFIER_RULE_STALE_DAYS", "90"))))
    except ValueError:
        return 90


def get_rules_snapshot_path() -> Path:
    """Путь файла-снапшота выученных правил (env ``CLASSIFIER_RULES_SNAPSHOT_PATH``).

    Пишется beat-джобой ``snapshot_learned_rules`` ежедневно. Путь должен быть
    **untracked** (дефолт под ``logs/``): tracked-файл, перезаписанный на проде,
    сломал бы ``git pull`` грязным деревом (PR-only, ADR-0002). Захват снапшота
    в git-историю — шагом dev-сессии (см. docs/ops/hitl-classifier-routine.md).
    """
    raw = (os.getenv("CLASSIFIER_RULES_SNAPSHOT_PATH") or "").strip()
    return Path(raw) if raw else Path("logs") / "classifier_learned_rules_snapshot.md"


def headless_enabled() -> bool:
    """Включена ли headless-классификация на DeepSeek (env ``CLASSIFIER_HEADLESS_ENABLED``).

    **Выключено по умолчанию, и это не осторожность ради осторожности.** Пока
    работает облачная рутина, оба источника пишут в одну таблицу, а
    ``record_verdicts`` идемпотентен по ``lip`` — кто успел, того и вердикт.
    Включать headless можно только ВМЕСТО рутины, иначе прогоны воруют друг у
    друга посты, счёт идёт за оба, а сверка «кто как решил» становится
    невозможной: на одном посте вердикт ровно один.

    Порядок перехода: сверка на общих постах (``scripts/classifier_headless_compare.py``,
    ничего не пишет) → выключить рутину → поднять этот флаг → через неделю
    удалить рутину.
    """
    return os.getenv("CLASSIFIER_HEADLESS_ENABLED", "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def get_headless_chunk_size() -> int:
    """Постов в одном вызове модели (env ``CLASSIFIER_HEADLESS_CHUNK``). Границы 1..40."""
    try:
        return max(1, min(40, int(os.getenv("CLASSIFIER_HEADLESS_CHUNK", "10"))))
    except ValueError:
        return 10


def read_postulates() -> str:
    """Текст файла-корректировщика (для промпта рутины/API). Нет файла → ''."""
    try:
        return POSTULATES_PATH.read_text(encoding="utf-8")
    except OSError:
        return ""


# Показатель степени при просмотрах в рейтинге отбора (звено 5, шаг 1).
# score = engagement / (views + 1) ** alpha
#   alpha = 0.5 — нынешняя post_popularity (рейтинг вовлечённости: маленькая
#                 группа с высоким откликом обгоняет районный хит)
#   alpha = 0.25 — дефолт: охват весомее, но не решает в одиночку
#   alpha = 0   — чистый абсолютный охват
# Наружу вынесен сознательно: это редакционная настройка, её подбирают на
# данных через витрину /api/classifier-review/rating/top, а не в коде.
RATING_VIEWS_ALPHA_DEFAULT = 0.25


# Границы разумного для показателя степени. 0 — чистый охват, 1 — просмотры в
# знаменателе в полную силу (сильнее нынешней 0.5). Всё, что вне, формулу не
# настраивает, а ломает: alpha < 0 переворачивает смысл (охват начинает
# УМНОЖАТЬСЯ и районный хит забивает всё), alpha > 1 схлопывает верхушку в
# посты с двумя просмотрами.
RATING_VIEWS_ALPHA_MIN = 0.0
RATING_VIEWS_ALPHA_MAX = 1.0


def get_rating_views_alpha() -> float:
    """Показатель степени при просмотрах (env ``RATING_VIEWS_ALPHA``).

    Нечитаемое значение — это дефолт, а не исключение: опечатка в env не
    должна ронять отбор посреди волны публикации.

    Тем же дефолтом читается и значение вне диапазона — включая ``nan`` и
    ``inf``, которые ``float()`` принимает молча. ``nan`` особенно тих: все
    сравнения с ним ложны, сортировка витрины перестала бы упорядочивать
    что-либо, и выглядело бы это как «рейтинг почему-то странный», а не как
    ошибка в env.
    """
    raw = (os.getenv("RATING_VIEWS_ALPHA") or "").strip()
    if not raw:
        return RATING_VIEWS_ALPHA_DEFAULT
    try:
        value = float(raw)
    except ValueError:
        return RATING_VIEWS_ALPHA_DEFAULT
    # not (min <= value <= max) вместо or-цепочки: так же ловится nan, для
    # которого любое сравнение даёт False.
    if not (RATING_VIEWS_ALPHA_MIN <= value <= RATING_VIEWS_ALPHA_MAX):
        logger.warning(
            "RATING_VIEWS_ALPHA=%r вне диапазона %s..%s — берём дефолт %s",
            raw,
            RATING_VIEWS_ALPHA_MIN,
            RATING_VIEWS_ALPHA_MAX,
            RATING_VIEWS_ALPHA_DEFAULT,
        )
        return RATING_VIEWS_ALPHA_DEFAULT
    return value
