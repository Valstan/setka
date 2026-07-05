"""Доменное ядро HITL-классификатора: операции над БД (ADR-0003).

Источник постов — свод­ки (``bulletin_curation_runs.candidates``): активный
конвейер SARAFAN не пишет пер-пост Post-строки (таблица posts пуста), а копит
кандидатов внутри свод­ок. Каждый кандидат — ``{lip, url, text, post_id,
owner_id, has_media}``; ключ идентичности — ``lip``. Общее для облачной рутины
(этап B) и будущего Claude-API-пути.

Операции:
- ``fetch_pending`` — кандидаты свод­ок без вердикта (для рутины);
- ``record_verdicts`` — записать вердикты (со снапшотом текста/url);
- ``review_feed`` — вердикты для операторской ленты;
- ``set_reaction`` / ``agree_all`` / ``correct`` — лог реакции оператора;
- ``agree_rate_stats`` — метрика shadow-гейта по типам.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import delete, func, select

from database.models_extended import (
    BulletinCurationRun,
    ClassificationCorrection,
    ContentClassification,
)
from modules.classifier.schema import VERDICT_TYPES, ClassifierVerdict

logger = logging.getLogger(__name__)

# Окно, за которое смотрим свод­ки как источник кандидатов.
DEFAULT_SOURCE_DAYS = 7


def _candidate_map(runs: Sequence[BulletinCurationRun]) -> Dict[str, Dict[str, Any]]:
    """Свести кандидатов из свод­ок в map lip → снапшот (дедуп по lip, новейшее первым)."""
    out: Dict[str, Dict[str, Any]] = {}
    for run in runs:
        cands = run.candidates or []
        if not isinstance(cands, (list, tuple)):
            continue
        for c in cands:
            if not isinstance(c, dict):
                continue
            lip = str(c.get("lip") or "").strip()
            if not lip or lip in out:
                continue
            out[lip] = {
                "lip": lip,
                "region_code": run.region_code,
                "text": (c.get("text") or "").strip(),
                "url": c.get("url") or "",
                "has_media": bool(c.get("has_media")),
            }
    return out


async def _recent_candidates(
    session,
    *,
    region_codes: Optional[Sequence[str]],
    days: int,
) -> Dict[str, Dict[str, Any]]:
    cutoff = datetime.utcnow() - timedelta(days=days)
    stmt = (
        select(BulletinCurationRun)
        .where(BulletinCurationRun.created_at >= cutoff)
        .order_by(BulletinCurationRun.created_at.desc())
    )
    if region_codes:
        stmt = stmt.where(BulletinCurationRun.region_code.in_(list(region_codes)))
    runs = (await session.execute(stmt)).scalars().all()
    return _candidate_map(runs)


async def fetch_pending(
    session,
    *,
    region_codes: Optional[Sequence[str]] = None,
    limit: int = 40,
    days: int = DEFAULT_SOURCE_DAYS,
) -> List[Dict[str, Any]]:
    """Кандидаты свод­ок без вердикта, одним батчем (рутина видит их вместе → merge)."""
    cand = await _recent_candidates(session, region_codes=region_codes, days=days)
    if not cand:
        return []
    classified = {
        lip
        for (lip,) in (
            await session.execute(
                select(ContentClassification.lip).where(
                    ContentClassification.lip.in_(list(cand.keys()))
                )
            )
        ).all()
    }
    fresh = [c for lip, c in cand.items() if lip not in classified]
    return fresh[:limit]


async def record_verdicts(
    session,
    verdicts: Sequence[ClassifierVerdict],
    *,
    source: str = "routine",
    region_codes_fallback: Optional[Sequence[str]] = None,
) -> Dict[str, int]:
    """Записать вердикты (shadow). Снапшот текста/url — из эхо рутины, иначе добор из свод­ок.

    Идемпотентно по ``lip`` (``skipped_existing``). Если регион не определить —
    ``skipped_missing`` (region_code NOT NULL).
    """
    if not verdicts:
        return {"recorded": 0, "skipped_existing": 0, "skipped_missing": 0}

    lips = [v.lip for v in verdicts]
    already = {
        lip
        for (lip,) in (
            await session.execute(
                select(ContentClassification.lip).where(ContentClassification.lip.in_(lips))
            )
        ).all()
    }

    # Добор снапшота/региона для вердиктов без эха.
    need_lookup = any(not (v.region_code and v.text) for v in verdicts)
    cand: Dict[str, Dict[str, Any]] = {}
    if need_lookup:
        cand = await _recent_candidates(
            session, region_codes=region_codes_fallback, days=DEFAULT_SOURCE_DAYS
        )

    recorded = skipped_existing = skipped_missing = 0
    for v in verdicts:
        if v.lip in already:
            skipped_existing += 1
            continue
        snap = cand.get(v.lip, {})
        region = (v.region_code or snap.get("region_code") or "").strip()
        if not region:
            skipped_missing += 1
            continue
        session.add(
            ContentClassification(
                lip=v.lip,
                region_code=region,
                post_text=(v.text or snap.get("text") or "").strip() or None,
                post_url=(v.url or snap.get("url") or "") or None,
                source=source,
                model=v.model,
                verdict=v.to_verdict_json(),
                confidence=int(v.confidence),
                shadow=True,
                escalated=False,
            )
        )
        already.add(v.lip)
        recorded += 1

    await session.commit()
    logger.info(
        "classifier: recorded=%s skipped_existing=%s skipped_missing=%s source=%s",
        recorded,
        skipped_existing,
        skipped_missing,
        source,
    )
    return {
        "recorded": recorded,
        "skipped_existing": skipped_existing,
        "skipped_missing": skipped_missing,
    }


# ---------------------------------------------------------------------------
# Операторская лента + реакции
# ---------------------------------------------------------------------------


async def review_feed(
    session,
    *,
    region_code: Optional[str] = None,
    only_unreacted: bool = True,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """Вердикты + снапшот текста для операторской ленты (свежие первыми)."""
    reacted = select(ClassificationCorrection.classification_id)
    stmt = (
        select(ContentClassification).order_by(ContentClassification.created_at.desc()).limit(limit)
    )
    if region_code:
        stmt = stmt.where(ContentClassification.region_code == region_code)
    if only_unreacted:
        stmt = stmt.where(ContentClassification.id.notin_(reacted))
    rows = (await session.execute(stmt)).scalars().all()
    return [c.to_dict() for c in rows]


async def _get_classification(session, classification_id: int) -> Optional[ContentClassification]:
    return await session.get(ContentClassification, classification_id)


async def set_reaction(
    session,
    *,
    classification_id: int,
    lip: str,
    verdict_type: str,
    outcome: str,
    ai_value: Any = None,
    operator_value: Any = None,
) -> None:
    """Идемпотентно записать одну реакцию на (classification, verdict_type).

    Предыдущая реакция того же типа удаляется (последняя побеждает) — так
    agree-rate по типу считается чисто, без двойного учёта.
    """
    await session.execute(
        delete(ClassificationCorrection).where(
            ClassificationCorrection.classification_id == classification_id,
            ClassificationCorrection.verdict_type == verdict_type,
        )
    )
    session.add(
        ClassificationCorrection(
            classification_id=classification_id,
            lip=lip,
            verdict_type=verdict_type,
            outcome=outcome,
            ai_value=ai_value,
            operator_value=operator_value,
        )
    )


def _applicable_types(verdict: Dict[str, Any]) -> List[str]:
    """Типы, по которым у вердикта есть суждение (theme/action всегда; merge — при сигнале)."""
    types = ["theme", "action"]
    if verdict.get("merge_with") or verdict.get("split"):
        types.append("merge")
    return types


async def agree_all(session, classification_id: int) -> Dict[str, Any]:
    """✅ «Согласен»: agree по всем применимым типам вердикта."""
    cls = await _get_classification(session, classification_id)
    if cls is None:
        return {"ok": False, "error": "classification not found"}
    verdict = cls.verdict or {}
    types = _applicable_types(verdict)
    for t in types:
        await set_reaction(
            session,
            classification_id=cls.id,
            lip=cls.lip,
            verdict_type=t,
            outcome="agree",
            ai_value=_ai_value_for_type(verdict, t),
        )
    await session.commit()
    return {"ok": True, "classification_id": cls.id, "agreed_types": types}


async def correct(
    session,
    classification_id: int,
    *,
    verdict_type: str,
    operator_value: Any,
) -> Dict[str, Any]:
    """Поправка одного аспекта вердикта (theme|action|merge)."""
    if verdict_type not in VERDICT_TYPES:
        return {"ok": False, "error": f"unknown verdict_type: {verdict_type}"}
    cls = await _get_classification(session, classification_id)
    if cls is None:
        return {"ok": False, "error": "classification not found"}
    verdict = cls.verdict or {}
    await set_reaction(
        session,
        classification_id=cls.id,
        lip=cls.lip,
        verdict_type=verdict_type,
        outcome="correct",
        ai_value=_ai_value_for_type(verdict, verdict_type),
        operator_value=operator_value,
    )
    await session.commit()
    return {"ok": True, "classification_id": cls.id, "verdict_type": verdict_type}


def _ai_value_for_type(verdict: Dict[str, Any], verdict_type: str) -> Any:
    if verdict_type == "theme":
        return verdict.get("theme")
    if verdict_type == "action":
        return verdict.get("action")
    if verdict_type == "merge":
        return {"merge_with": verdict.get("merge_with") or [], "split": bool(verdict.get("split"))}
    return None


async def agree_rate_stats(session) -> Dict[str, Any]:
    """agree-rate по каждому типу вердикта (метрика shadow-гейта, ADR-0003 §F)."""
    rows = (
        await session.execute(
            select(
                ClassificationCorrection.verdict_type,
                ClassificationCorrection.outcome,
                func.count().label("n"),
            ).group_by(
                ClassificationCorrection.verdict_type,
                ClassificationCorrection.outcome,
            )
        )
    ).all()
    agg: Dict[str, Dict[str, int]] = {t: {"agree": 0, "correct": 0} for t in VERDICT_TYPES}
    for vtype, outcome, n in rows:
        if vtype in agg and outcome in ("agree", "correct"):
            agg[vtype][outcome] = int(n or 0)

    out = {}
    for t in VERDICT_TYPES:
        a = agg[t]["agree"]
        c = agg[t]["correct"]
        total = a + c
        out[t] = {
            "agree": a,
            "correct": c,
            "total": total,
            "agree_rate": round(a / total, 3) if total else None,
        }

    total_classified = (
        await session.execute(select(func.count(ContentClassification.id)))
    ).scalar() or 0
    return {"total_classified": int(total_classified), "by_type": out}
