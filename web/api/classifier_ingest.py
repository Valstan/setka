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
    read_postulates,
)
from database.connection import AsyncSessionLocal
from modules.classifier import service
from modules.classifier.schema import VerdictBatch

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
        posts = await service.fetch_pending(session, region_codes=codes or None, limit=n)
    return {"region_filter": codes, "count": len(posts), "posts": posts}


@router.get("/postulates", response_class=PlainTextResponse)
async def postulates():
    """Текст файла-корректировщика (рутина вставляет его в промпт)."""
    _check_enabled()
    return read_postulates()


@router.post("/verdicts")
async def verdicts(batch: VerdictBatch, _auth: None = Depends(require_ingest_key)):
    """Принять пакет вердиктов от рутины → записать в content_classifications."""
    _check_enabled()
    async with AsyncSessionLocal() as session:
        counts = await service.record_verdicts(session, batch.verdicts, source="routine")
    return {"ok": True, **counts}
