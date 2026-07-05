"""Доменное ядро HITL-классификатора: операции над БД (ADR-0003).

Общее для облачной рутины (этап B) и будущего Claude-API-пути. Чистая логика
поверх async-session — HTTP-слой в ``web/api/classifier_ingest.py`` /
``web/api/classifier_review.py``.

Операции:
- ``fetch_pending`` — посты одного/нескольких районов без вердикта (для рутины);
- ``record_verdicts`` — записать вердикты в ``content_classifications``;
- ``review_feed`` — посты+вердикты для операторской ленты;
- ``set_reaction`` / ``agree_all`` / ``correct`` — лог реакции оператора;
- ``agree_rate_stats`` — метрика shadow-гейта по типам.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import delete, func, select

from database.models import Post, Region
from database.models_extended import ClassificationCorrection, ContentClassification
from modules.classifier.schema import VERDICT_TYPES, ClassifierVerdict

logger = logging.getLogger(__name__)

# Статусы постов, которые классифицируем в shadow (свежий поток парса).
PENDING_STATUSES = ("new", "analyzed")


def _post_payload(post: Post, region_code: str) -> Dict[str, Any]:
    """Компактное представление поста для промпта рутины."""
    atts = post.attachments or []
    has_media = bool(atts) if isinstance(atts, (list, tuple)) else bool(atts)
    return {
        "post_id": post.id,
        "region_code": region_code,
        "text": (post.text or "").strip(),
        "has_media": has_media,
        "date": post.date_published.isoformat() if post.date_published else None,
    }


async def fetch_pending(
    session,
    *,
    region_codes: Optional[Sequence[str]] = None,
    limit: int = 40,
) -> List[Dict[str, Any]]:
    """Посты без вердикта (свежие, статус new/analyzed), одним батчем.

    Батч отдаётся рутине целиком, чтобы она видела посты вместе и могла
    предлагать ``merge_with`` внутри батча (склейка по смыслу). Уже
    классифицированные исключаются анти-джойном по ``content_classifications``.
    """
    classified = select(ContentClassification.post_id)
    stmt = (
        select(Post, Region.code)
        .join(Region, Post.region_id == Region.id)
        .where(
            Post.status.in_(PENDING_STATUSES),
            Post.id.notin_(classified),
        )
        .order_by(Post.date_published.desc())
        .limit(limit)
    )
    if region_codes:
        stmt = stmt.where(Region.code.in_(list(region_codes)))
    rows = (await session.execute(stmt)).all()
    return [_post_payload(post, code) for post, code in rows]


async def record_verdicts(
    session,
    verdicts: Sequence[ClassifierVerdict],
    *,
    source: str = "routine",
) -> Dict[str, int]:
    """Записать вердикты в ``content_classifications`` (shadow, Post не трогаем).

    Идемпотентно: посты, у которых вердикт уже есть, пропускаются
    (``skipped_existing``). Несуществующие post_id — ``skipped_missing``.
    """
    if not verdicts:
        return {"recorded": 0, "skipped_existing": 0, "skipped_missing": 0}

    post_ids = [v.post_id for v in verdicts]

    # region_code для каждого поста + проверка существования.
    region_by_post: Dict[int, str] = {}
    rows = (
        await session.execute(
            select(Post.id, Region.code)
            .join(Region, Post.region_id == Region.id)
            .where(Post.id.in_(post_ids))
        )
    ).all()
    for pid, code in rows:
        region_by_post[pid] = code

    already = {
        pid
        for (pid,) in (
            await session.execute(
                select(ContentClassification.post_id).where(
                    ContentClassification.post_id.in_(post_ids)
                )
            )
        ).all()
    }

    recorded = skipped_existing = skipped_missing = 0
    for v in verdicts:
        if v.post_id not in region_by_post:
            skipped_missing += 1
            continue
        if v.post_id in already:
            skipped_existing += 1
            continue
        session.add(
            ContentClassification(
                post_id=v.post_id,
                region_code=region_by_post[v.post_id],
                source=source,
                model=v.model,
                verdict=v.to_verdict_json(),
                confidence=int(v.confidence),
                shadow=True,
                escalated=False,
            )
        )
        already.add(v.post_id)
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
    """Вердикты + тексты постов для операторской ленты (свежие первыми).

    ``only_unreacted`` — показывать только те, по которым оператор ещё не
    высказался (нет ни одной строки в ``classification_corrections``).
    """
    reacted = select(ClassificationCorrection.classification_id)
    stmt = (
        select(ContentClassification, Post.text, Post.date_published)
        .join(Post, ContentClassification.post_id == Post.id)
        .order_by(ContentClassification.created_at.desc())
        .limit(limit)
    )
    if region_code:
        stmt = stmt.where(ContentClassification.region_code == region_code)
    if only_unreacted:
        stmt = stmt.where(ContentClassification.id.notin_(reacted))
    rows = (await session.execute(stmt)).all()
    out: List[Dict[str, Any]] = []
    for cls, text, date in rows:
        d = cls.to_dict()
        d["post_text"] = (text or "").strip()
        d["post_date"] = date.isoformat() if date else None
        out.append(d)
    return out


async def _get_classification(session, classification_id: int) -> Optional[ContentClassification]:
    return await session.get(ContentClassification, classification_id)


async def set_reaction(
    session,
    *,
    classification_id: int,
    post_id: int,
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
            post_id=post_id,
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
    for t in _applicable_types(verdict):
        ai_val = _ai_value_for_type(verdict, t)
        await set_reaction(
            session,
            classification_id=cls.id,
            post_id=cls.post_id,
            verdict_type=t,
            outcome="agree",
            ai_value=ai_val,
        )
    await session.commit()
    return {"ok": True, "classification_id": cls.id, "agreed_types": _applicable_types(verdict)}


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
        post_id=cls.post_id,
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
