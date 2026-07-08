"""HTTP-интерфейс HITL-классификатора для облачной рутины (``/api/classifier``).

Этап B (ADR-0003, решение владельца 2026-07-05): классификацию делает
scheduled cloud agent (Claude Code routine), пока нет ``ANTHROPIC_API_KEY``.
Рутина ходит сюда:
- ``GET  /api/classifier/pending``   — забрать батч постов без вердикта;
- ``GET  /api/classifier/postulates``— текст файла-корректировщика для промпта;
- ``POST /api/classifier/verdicts``  — вернуть вердикты.

Защита — API-ключ рутины (``X-API-Key`` = env ``CLASSIFIER_INGEST_KEY``,
constant-time), как VK-шлюз. Путь в ``PUBLIC_PREFIXES`` (своя защита, не
сессия). Kill-switch ``CLASSIFIER_DISABLED`` → 503. Когда появится Claude API,
этот ingest уступит место Celery-таску (та же ``modules.classifier.service``),
а роутер можно выключить ключом.
"""

from __future__ import annotations

import hmac
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import PlainTextResponse

from config.classifier import (
    classifier_disabled,
    get_ingest_key,
    get_pending_max,
    get_region_allowlist,
    get_source_days,
)
from database.connection import AsyncSessionLocal
from modules.classifier import rules, service
from modules.classifier.schema import RuleProposalBatch, VerdictBatch

logger = logging.getLogger(__name__)
router = APIRouter()


def _check_enabled() -> None:
    if classifier_disabled():
        raise HTTPException(status_code=503, detail="classifier disabled")


async def require_ingest_key(
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
) -> None:
    """Проверить ключ рутины (constant-time). Ключ не задан в env → 503 (ingest выкл.)."""
    expected = get_ingest_key()
    if not expected:
        raise HTTPException(status_code=503, detail="classifier ingest not configured")
    if not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(status_code=401, detail="bad or missing X-API-Key")


@router.get("/pending")
async def pending(
    region: str = Query("", description="код района; пусто = allowlist из env"),
    limit: int = Query(0, ge=0, le=200),
    _auth: None = Depends(require_ingest_key),
):
    """Батч постов без вердикта для классификации рутиной.

    region пуст → берём allowlist ``CLASSIFIER_REGION_CODES`` (обкатка на одном
    районе). Батч отдаётся целиком, чтобы рутина видела посты вместе (merge).
    """
    _check_enabled()
    codes = [region.strip()] if region.strip() else get_region_allowlist()
    cap = get_pending_max()
    n = min(limit or cap, cap)
    async with AsyncSessionLocal() as session:
        posts = await service.fetch_pending(
            session, region_codes=codes or None, limit=n, days=get_source_days()
        )
    return {"region_filter": codes, "count": len(posts), "posts": posts}


@router.get("/postulates", response_class=PlainTextResponse)
async def postulates():
    """Эффективные постулаты для промпта: базовый git-файл + утверждённые выученные
    правила (overlay, ADR-0005). Рутина вставляет результат в промпт классификации."""
    _check_enabled()
    async with AsyncSessionLocal() as session:
        return await rules.render_effective_postulates(session)


@router.post("/verdicts")
async def verdicts(batch: VerdictBatch, _auth: None = Depends(require_ingest_key)):
    """Принять пакет вердиктов от рутины → записать в content_classifications."""
    _check_enabled()
    async with AsyncSessionLocal() as session:
        counts = await service.record_verdicts(
            session,
            batch.verdicts,
            source="routine",
            region_codes_fallback=get_region_allowlist() or None,
        )
    return {"ok": True, **counts}


# --- Петля обучения (ADR-0005): дистилляция коррекций → черновики правил --------


@router.get("/corrections")
async def corrections(
    limit: int = Query(100, ge=1, le=500),
    days: int = Query(30, ge=1, le=180),
    _auth: None = Depends(require_ingest_key),
):
    """Коррекции оператора (несогласия) + снапшот поста — сырьё для рутины-дистиллятора.

    Рутина смотрит на них + текущие эффективные правила (``/postulates``) и предлагает
    обобщённые правила через ``POST /rule-proposals``."""
    _check_enabled()
    async with AsyncSessionLocal() as session:
        items = await rules.fetch_corrections_for_distill(session, limit=limit, days=days)
    return {"count": len(items), "corrections": items}


@router.post("/rule-proposals")
async def rule_proposals(batch: RuleProposalBatch, _auth: None = Depends(require_ingest_key)):
    """Принять черновики выученных правил от рутины → записать как ``proposed``.

    Не применяются автоматически: оператор утверждает/правит/отклоняет в ленте
    ``/classifier`` (человек в петле, ADR-0005). Дедуп против активных правил."""
    _check_enabled()
    async with AsyncSessionLocal() as session:
        counts = await rules.record_rule_proposals(session, batch.proposals, source="routine")
    return {"ok": True, **counts}
