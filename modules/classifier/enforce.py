"""Enforce вердиктов HITL-классификатора на публикацию (заказ владельца 2026-07-13).

Shadow-фаза классификатора только писала вердикты; этот модуль замыкает петлю:
посты с эффективным действием ``delete`` или ``hold`` НЕ попадают в сводку.

«Эффективное действие» уважает HITL: правка оператора (verdict_type='action',
outcome='correct') главнее вердикта ИИ — если оператор перевёл пост в publish,
пост публикуется, что бы ни решила нейронка. Оператор продолжает дообучать
систему через ленту ``/classifier`` — enforcement её не подменяет.

Fail-open: любой сбой (БД, JSON) → пустой блок-набор, публикация живёт по
обычным фильтрам. Классификатор — усилитель, не единая точка отказа.

Env:
  CLASSIFIER_ENFORCE_ENABLED=0        # выключен по умолчанию (включаем на проде)
  CLASSIFIER_ENFORCE_REGION_CODES     # CSV; пусто = все регионы
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import List, Set

from sqlalchemy import select

logger = logging.getLogger(__name__)


def enforce_enabled() -> bool:
    """Флаг enforcement'а (env ``CLASSIFIER_ENFORCE_ENABLED``). Дефолт — выкл."""
    return os.getenv("CLASSIFIER_ENFORCE_ENABLED", "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def get_enforce_region_allowlist() -> List[str]:
    """Регионы, где вердикты фильтруют публикацию (CSV; пусто = все)."""
    raw = os.getenv("CLASSIFIER_ENFORCE_REGION_CODES", "") or ""
    return [c.strip() for c in raw.replace(";", ",").split(",") if c.strip()]


async def fetch_blocked_lips(session, region_code: str) -> Set[str]:
    """Вернуть lip'ы, которые нейро-вердикт выключает из публикации региона.

    Блокируются посты с эффективным действием ``delete`` / ``hold``
    (правка оператора главнее ИИ). Окно свежести — как у источника
    классификатора (``CLASSIFIER_SOURCE_DAYS``): старше него посты и так
    не попадают в сводку.

    Вердикт применяется ТОЛЬКО к региону, в контексте которого он вынесен
    (``ContentClassification.region_code``): гео-относительные правила
    («чужой район → delete») дают противоположные вердикты для разных
    регионов, глобальный lip-набор заражал бы соседей. Пост без вердикта
    в своём регионе публикуется по обычным фильтрам (fail-open).

    Fail-open: ошибка чтения → пустой set + warning.
    """
    if not enforce_enabled():
        return set()
    allow = get_enforce_region_allowlist()
    if allow and region_code not in allow:
        return set()
    try:
        from config.classifier import get_source_days
        from database.models_extended import ClassificationCorrection, ContentClassification

        cutoff = datetime.utcnow() - timedelta(days=get_source_days())
        rows = (
            await session.execute(
                select(
                    ContentClassification.id,
                    ContentClassification.lip,
                    ContentClassification.verdict,
                ).where(
                    ContentClassification.created_at >= cutoff,
                    ContentClassification.region_code == region_code,
                )
            )
        ).all()
        if not rows:
            return set()

        # Правки оператора по типу 'action' (outcome='correct'): id → operator_value.
        corr_rows = (
            await session.execute(
                select(
                    ClassificationCorrection.classification_id,
                    ClassificationCorrection.operator_value,
                ).where(
                    ClassificationCorrection.classification_id.in_([r[0] for r in rows]),
                    ClassificationCorrection.verdict_type == "action",
                    ClassificationCorrection.outcome == "correct",
                )
            )
        ).all()
        operator_action = {cid: val for cid, val in corr_rows}

        blocked: Set[str] = set()
        for cid, lip, verdict in rows:
            action = operator_action.get(cid)
            if action is None:
                action = ((verdict or {}).get("action") or "").strip().lower()
            else:
                action = str(action or "").strip().lower()
            if action in ("delete", "hold"):
                blocked.add(lip)
        return blocked
    except Exception as e:  # defensive — enforcement никогда не роняет публикацию
        logger.warning("classifier enforce: fetch_blocked_lips failed — fail-open: %s", e)
        return set()
