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
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List

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
    ленту не попадало старьё, которое в сводку уже не пойдёт. Дефолт 3 (модель
    владельца «не старше 3 суток»). Считается по ``collected_at`` аудита сбора
    (прокси даты публикации: механически-старые посты фильтр отсекает ещё при
    сборе, поэтому собранное — свежее). Границы 1..30.
    """
    try:
        return max(1, min(30, int(os.getenv("CLASSIFIER_SOURCE_DAYS", "3"))))
    except ValueError:
        return 3


def read_postulates() -> str:
    """Текст файла-корректировщика (для промпта рутины/API). Нет файла → ''."""
    try:
        return POSTULATES_PATH.read_text(encoding="utf-8")
    except OSError:
        return ""
