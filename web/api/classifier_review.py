"""Операторская лента вердиктов HITL-классификатора (``/api/classifier-review``).

Доступ — только оператор (путь НЕ в ``PUBLIC_PREFIXES``, закрыт сессионной
cookie ``AuthGateMiddleware``), в отличие от ingest-интерфейса рутины
``/api/classifier`` (тот со своей X-API-Key защитой). Аналогично паре
gateway / gateway-stats.

Оператор смотрит пост + вердикт нейронки и реагирует: ✅ согласен / изменить
тему / поменять действие / поправить склейку. Несогласия → лог коррекций
(сырьё agree-rate + дистилляции файла-корректировщика).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Body, Query
from pydantic import BaseModel

from database.connection import AsyncSessionLocal
from modules.classifier import service

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/feed")
async def feed(
    region: str = Query("", description="код района (опционально)"),
    only_unreviewed: bool = Query(True, description="только неразобранные (не финализированные)"),
    limit: int = Query(50, ge=1, le=200),
):
    """Лента: пост + вердикт нейронки для оператора."""
    async with AsyncSessionLocal() as session:
        items = await service.review_feed(
            session,
            region_code=region.strip() or None,
            only_unreviewed=only_unreviewed,
            limit=limit,
        )
    return {"count": len(items), "items": items}


@router.post("/{classification_id}/agree")
async def agree(classification_id: int):
    """✅ «Согласен со всем» — agree по всем типам + финализация (пост уходит из ленты)."""
    async with AsyncSessionLocal() as session:
        out = await service.agree_all(session, classification_id)
    return out


@router.post("/{classification_id}/finalize")
async def finalize(classification_id: int):
    """✔ «Готово» — завершить составной вердикт: правки сохранить, остальное = agree."""
    async with AsyncSessionLocal() as session:
        out = await service.finalize(session, classification_id)
    return out


class CorrectionIn(BaseModel):
    verdict_type: str  # theme | action | merge
    operator_value: Any = None


@router.post("/{classification_id}/correct")
async def correct(classification_id: int, body: CorrectionIn = Body(...)):
    """Поправка одного аспекта вердикта (theme|action|merge)."""
    async with AsyncSessionLocal() as session:
        out = await service.correct(
            session,
            classification_id,
            verdict_type=body.verdict_type,
            operator_value=body.operator_value,
        )
    return out


@router.get("/stats")
async def stats():
    """agree-rate по типам вердикта — метрика shadow-гейта (ADR-0003 §F)."""
    async with AsyncSessionLocal() as session:
        return await service.agree_rate_stats(session)
