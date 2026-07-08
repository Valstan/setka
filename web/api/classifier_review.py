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
from typing import Any, Optional

from fastapi import APIRouter, Body, Query
from pydantic import BaseModel

from database.connection import AsyncSessionLocal
from modules.classifier import rules, service

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


# --- Петля обучения (ADR-0005): оператор утверждает выученные правила ------------


@router.get("/rules")
async def rules_list(
    status: str = Query("", description="proposed|approved|rejected|retired; пусто = все"),
):
    """Выученные правила для операторской панели (черновики + утверждённые)."""
    async with AsyncSessionLocal() as session:
        items = await rules.list_rules(session, status=status.strip() or None)
    return {"count": len(items), "rules": items}


class RuleDecision(BaseModel):
    edited_text: Optional[str] = None  # опц. правка текста при утверждении


@router.post("/rules/{rule_id}/approve")
async def rule_approve(rule_id: int, body: RuleDecision = Body(default=RuleDecision())):
    """✅ Утвердить черновик правила (опц. с правкой текста) → в деле со след. прогона."""
    async with AsyncSessionLocal() as session:
        return await rules.decide_rule(
            session, rule_id, status="approved", edited_text=body.edited_text
        )


@router.post("/rules/{rule_id}/reject")
async def rule_reject(rule_id: int):
    """❌ Отклонить черновик правила."""
    async with AsyncSessionLocal() as session:
        return await rules.decide_rule(session, rule_id, status="rejected")


@router.post("/rules/{rule_id}/retire")
async def rule_retire(rule_id: int):
    """🗑 Вывести утверждённое правило из обращения (aging)."""
    async with AsyncSessionLocal() as session:
        return await rules.decide_rule(session, rule_id, status="retired")


class RuleAdd(BaseModel):
    rule_text: str
    region_code: Optional[str] = None


@router.post("/rules/add")
async def rule_add(body: RuleAdd = Body(...)):
    """Оператор пишет правило руками → сразу утверждено (в деле со след. прогона)."""
    async with AsyncSessionLocal() as session:
        return await rules.add_operator_rule(session, body.rule_text, region_code=body.region_code)
