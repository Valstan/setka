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
    ClassifierTheme,
    ClassifierThemeAlias,
    CollectedPostAudit,
    ContentClassification,
)
from modules.classifier.schema import VERDICT_TYPES, ClassifierVerdict

logger = logging.getLogger(__name__)

# Окно, за которое смотрим свод­ки как источник кандидатов.
DEFAULT_SOURCE_DAYS = 7


async def _theme_canon_map(session) -> Dict[str, str]:
    """map «написание → канон» из словаря тем (069) и синонимов (079).

    Канон отображается сам на себя, чтобы одна проверка покрывала оба случая.
    Ключи — в нижнем регистре: регистр это самый частый вид расхождения
    («Православие» против «православие»), и лечить его отдельной строкой
    словаря было бы расточительно.
    """
    canon_rows = (await session.execute(select(ClassifierTheme.name))).scalars().all()
    out = {str(n).strip().lower(): str(n) for n in canon_rows if str(n or "").strip()}
    alias_rows = (
        await session.execute(select(ClassifierThemeAlias.alias, ClassifierThemeAlias.canon))
    ).all()
    for alias, canon in alias_rows:
        key = str(alias or "").strip().lower()
        if key and str(canon or "").strip():
            # Канон побеждает синоним: если оператор завёл тему, одноимённую
            # чужому синониму, тема остаётся собой.
            out.setdefault(key, str(canon))
    return out


def canonicalize_theme(theme: Any, canon_map: Dict[str, str]) -> Optional[str]:
    """Привести тему к канону. Неизвестное значение возвращается как есть.

    Возвращает ``None`` для пустой темы. **Неизвестное не подгоняется** — по
    тому же доводу, что и в конвейере: молча подогнанный поток не сообщает
    ничего, а расхождение нужно видеть, чтобы завести синоним осознанно.
    """
    raw = str(theme or "").strip()
    if not raw:
        return None
    return canon_map.get(raw.lower(), raw)


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
                "media": [],  # свод­ки-кандидаты вложений не несут (только аудит)
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


async def _recent_audit(
    session,
    *,
    region_codes: Optional[Sequence[str]],
    days: int,
) -> Dict[str, Dict[str, Any]]:
    """Собранные посты из аудита сбора (ADR-0004) → map lip → снапшот с решением фильтра.

    В отличие от ``_recent_candidates`` (только опубликованное), видит ОБЕ стороны:
    ``decision`` = kept|dropped + ``drop_reason``. Новейшее первым, дедуп по lip.
    """
    cutoff = datetime.utcnow() - timedelta(days=days)
    stmt = (
        select(CollectedPostAudit)
        .where(CollectedPostAudit.collected_at >= cutoff)
        .order_by(CollectedPostAudit.collected_at.desc())
    )
    if region_codes:
        stmt = stmt.where(CollectedPostAudit.region_code.in_(list(region_codes)))
    rows = (await session.execute(stmt)).scalars().all()
    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        if r.lip in out:
            continue
        out[r.lip] = {
            "lip": r.lip,
            "region_code": r.region_code,
            "text": (r.post_text or "").strip(),
            "url": r.post_url or "",
            "has_media": bool(r.has_media),
            "media": r.media or [],
            "decision": r.decision,
            "drop_reason": r.drop_reason,
        }
    return out


async def _recent_source(
    session,
    *,
    region_codes: Optional[Sequence[str]],
    days: int,
) -> Dict[str, Dict[str, Any]]:
    """Источник постов для классификатора: аудит сбора (обе стороны, ADR-0004);
    если он пуст — журнал курации (только опубликованное, переходный период)."""
    audit = await _recent_audit(session, region_codes=region_codes, days=days)
    if audit:
        return audit
    return await _recent_candidates(session, region_codes=region_codes, days=days)


async def fetch_pending(
    session,
    *,
    region_codes: Optional[Sequence[str]] = None,
    limit: int = 40,
    days: int = DEFAULT_SOURCE_DAYS,
) -> List[Dict[str, Any]]:
    """Собранные посты без вердикта, одним батчем (рутина видит их вместе → merge).

    Источник — аудит сбора (обе стороны, ADR-0004) с fallback на журнал курации.
    """
    cand = await _recent_source(session, region_codes=region_codes, days=days)
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
    return _fair_regional_batch(fresh, limit)


def _fair_regional_batch(fresh: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    """Собрать батч так, чтобы регионы не перемешивались и никто не голодал.

    1) round-robin по регионам (свежие первыми внутри региона) — при backlog
       больше лимита каждый регион получает честную долю, а не «кто первый
       в куче»; 2) итоговый батч отсортирован блоками по региону — рутина
       классифицирует посты одного региона подряд, без чересполосицы
       (merge-кандидаты одного события живут в одном регионе).
    """
    # Внутри региона кандидаты на публикацию идут ПЕРЕД уже отсеянными
    # (решение владельца 2026-08-19). Аудит хранит обе стороны фильтра
    # намеренно (ADR-0004 — оператор должен видеть и ошибочный отсев), но при
    # заваленной очереди это значит, что часть токенов уходит на посты, которые
    # в сводку не попадут ни при каком вердикте: на 19.08 в окне свежести
    # 4893 kept против 1388 dropped, то есть 22% пропускной способности.
    # Порядок, а не исключение: dropped по-прежнему классифицируются, просто
    # после kept — иначе сломался бы разбор ложных отсевов.
    by_region: Dict[str, List[Dict[str, Any]]] = {}
    for c in fresh:  # fresh уже newest-first (порядок _recent_source)
        by_region.setdefault(str(c.get("region_code") or ""), []).append(c)
    for queue in by_region.values():
        # Стабильная сортировка: внутри каждой группы порядок «свежие первыми»
        # сохраняется. Посты без decision (источник — журнал курации, где
        # решения фильтра нет) считаются кандидатами: там всё опубликованное.
        queue.sort(key=lambda c: 1 if c.get("decision") == "dropped" else 0)

    picked: List[Dict[str, Any]] = []
    queues = [q for _, q in sorted(by_region.items())]
    while queues and len(picked) < limit:
        next_round = []
        for q in queues:
            if len(picked) >= limit:
                break
            picked.append(q.pop(0))
            if q:
                next_round.append(q)
        queues = next_round

    picked.sort(key=lambda c: str(c.get("region_code") or ""))
    return picked


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
        cand = await _recent_source(
            session, region_codes=region_codes_fallback, days=DEFAULT_SOURCE_DAYS
        )

    # Канон тем применяется НА ЗАПИСЬ, а не только разовой уборкой.
    # Миграция 070 нормализовала историю и на этом остановилась; за три недели
    # Корпус набрал 29 неканонических написаний, потому что дверь осталась без
    # замка. Тема — ось agree-rate, группировки правил и дистилляции, и её
    # расщепление обесценивает статистику молча.
    canon_map = await _theme_canon_map(session)
    unknown_themes: Dict[str, int] = {}

    recorded = skipped_existing = skipped_missing = 0
    # Вердикты, у которых движок положил действие в поле темы. Считаем отдельно:
    # INFO про «темы вне канона» их не покажет — тема уже обнулена, — и молчание
    # тут означало бы, что поток теряет тематизацию, а мы об этом не знаем.
    action_in_theme: List[str] = []
    for v in verdicts:
        if v.lip in already:
            skipped_existing += 1
            continue
        snap = cand.get(v.lip, {})
        region = (v.region_code or snap.get("region_code") or "").strip()
        if not region:
            skipped_missing += 1
            continue
        verdict_json = v.to_verdict_json()
        if not verdict_json.get("theme") and (v.theme or "").strip():
            action_in_theme.append(v.lip)
        theme = canonicalize_theme(verdict_json.get("theme"), canon_map)
        if theme is not None:
            verdict_json["theme"] = theme
            if theme.lower() not in canon_map:
                unknown_themes[theme] = unknown_themes.get(theme, 0) + 1
        session.add(
            ContentClassification(
                lip=v.lip,
                region_code=region,
                post_text=(v.text or snap.get("text") or "").strip() or None,
                post_url=(v.url or snap.get("url") or "") or None,
                source=source,
                model=v.model,
                verdict=verdict_json,
                confidence=int(v.confidence),
                shadow=True,
                escalated=False,
            )
        )
        already.add(v.lip)
        recorded += 1

    if action_in_theme:
        logger.warning(
            "классификатор: движок положил действие в поле темы — %s вердиктов, "
            "тема обнулена (lip: %s)",
            len(action_in_theme),
            ", ".join(action_in_theme[:10]),
        )

    if unknown_themes:
        # Не глушим: неизвестное написание — кандидат в синонимы, и увидеть
        # его должен человек, а не тихо переварить классификатор.
        logger.info(
            "классификатор: темы вне канона и словаря синонимов — %s",
            ", ".join(f"{t}×{n}" for t, n in sorted(unknown_themes.items(), key=lambda kv: -kv[1])),
        )

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
    only_unreviewed: bool = True,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """Вердикты + снапшот текста для операторской ленты (свежие первыми).

    ``only_unreviewed`` фильтрует по ``reviewed_at`` (явная финализация), НЕ по
    «есть ли реакция» — чтобы пост с частичной правкой (сменил тему, но ещё не
    завершил) оставался в ленте до «Готово» / «Согласен со всем».
    """
    stmt = (
        select(ContentClassification).order_by(ContentClassification.created_at.desc()).limit(limit)
    )
    if region_code:
        stmt = stmt.where(ContentClassification.region_code == region_code)
    if only_unreviewed:
        stmt = stmt.where(ContentClassification.reviewed_at.is_(None))
    rows = (await session.execute(stmt)).scalars().all()
    return await _attach_filter_decision(session, rows)


async def _attach_filter_decision(session, rows: Sequence[Any]) -> List[Dict[str, Any]]:
    """Строки вердиктов → dict'ы с решением детерминированного фильтра (ADR-0004).

    Оператор видит «🚫 отсеян: реклама» vs «✅ прошёл фильтр». Несогласие на
    dropped = сигнал пере-фильтрации, на kept = пере-публикации.
    """
    items = [c.to_dict() for c in rows]
    lips = [c.lip for c in rows]
    if lips:
        audit_rows = (
            await session.execute(
                select(
                    CollectedPostAudit.lip,
                    CollectedPostAudit.decision,
                    CollectedPostAudit.drop_reason,
                ).where(CollectedPostAudit.lip.in_(lips))
            )
        ).all()
        amap = {lip: (dec, reason) for lip, dec, reason in audit_rows}
        for item in items:
            dr = amap.get(item["lip"])
            item["filter_decision"] = dr[0] if dr else None
            item["filter_reason"] = dr[1] if dr else None
    return items


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


async def _reacted_types(session, classification_id: int) -> set:
    """Типы вердикта, по которым у поста уже есть реакция оператора."""
    rows = (
        await session.execute(
            select(ClassificationCorrection.verdict_type).where(
                ClassificationCorrection.classification_id == classification_id
            )
        )
    ).all()
    return {t for (t,) in rows}


async def agree_all(session, classification_id: int) -> Dict[str, Any]:
    """✅ «Согласен со всем»: agree по всем применимым типам + финализация.

    Перезаписывает любые частичные правки оператора (буквальный смысл — «согласен
    со всеми выводами ИИ»). Для сохранения правок есть ``finalize`` («Готово»).
    """
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
    cls.reviewed_at = datetime.utcnow()
    await session.commit()
    return {"ok": True, "classification_id": cls.id, "agreed_types": types, "reviewed": True}


async def finalize(session, classification_id: int) -> Dict[str, Any]:
    """✔ «Готово»: завершить вердикт, сохранив правки оператора.

    По каждому применимому типу БЕЗ явной реакции оператора пишем ``agree``;
    уже внесённые правки остаются как есть. Ставит ``reviewed_at`` → пост уходит
    из ленты. Это путь для СОСТАВНОГО вердикта (сменил тему, остальное принял).
    """
    cls = await _get_classification(session, classification_id)
    if cls is None:
        return {"ok": False, "error": "classification not found"}
    verdict = cls.verdict or {}
    reacted = await _reacted_types(session, cls.id)
    agreed = []
    for t in _applicable_types(verdict):
        if t not in reacted:
            await set_reaction(
                session,
                classification_id=cls.id,
                lip=cls.lip,
                verdict_type=t,
                outcome="agree",
                ai_value=_ai_value_for_type(verdict, t),
            )
            agreed.append(t)
    cls.reviewed_at = datetime.utcnow()
    await session.commit()
    return {"ok": True, "classification_id": cls.id, "auto_agreed_types": agreed, "reviewed": True}


async def correct(
    session,
    classification_id: int,
    *,
    verdict_type: str,
    operator_value: Any,
) -> Dict[str, Any]:
    """Поправка одного аспекта вердикта (theme|action|merge). НЕ финализирует.

    Если правка оператора совпала со значением ИИ (напр. клик «→ публиковать» на
    посте, где ИИ уже поставил publish) — это согласие, пишем ``agree``, а не
    ложную коррекцию (иначе agree-rate занижается). Карточка остаётся в ленте до
    финализации.
    """
    if verdict_type not in VERDICT_TYPES:
        return {"ok": False, "error": f"unknown verdict_type: {verdict_type}"}
    cls = await _get_classification(session, classification_id)
    if cls is None:
        return {"ok": False, "error": "classification not found"}
    verdict = cls.verdict or {}
    ai_value = _ai_value_for_type(verdict, verdict_type)
    outcome = "agree" if _values_agree(verdict_type, ai_value, operator_value) else "correct"
    await set_reaction(
        session,
        classification_id=cls.id,
        lip=cls.lip,
        verdict_type=verdict_type,
        outcome=outcome,
        ai_value=ai_value,
        operator_value=operator_value,
    )
    await session.commit()
    return {
        "ok": True,
        "classification_id": cls.id,
        "verdict_type": verdict_type,
        "outcome": outcome,
    }


def _ai_value_for_type(verdict: Dict[str, Any], verdict_type: str) -> Any:
    if verdict_type == "theme":
        return verdict.get("theme")
    if verdict_type == "action":
        return verdict.get("action")
    if verdict_type == "merge":
        return {"merge_with": verdict.get("merge_with") or [], "split": bool(verdict.get("split"))}
    return None


def _values_agree(verdict_type: str, ai_value: Any, operator_value: Any) -> bool:
    """Совпадает ли правка оператора со значением ИИ (тогда это согласие, не правка)."""
    if operator_value is None:
        return False
    if verdict_type in ("theme", "action"):
        return (
            str(ai_value or "").strip().casefold() == str(operator_value or "").strip().casefold()
        )
    if verdict_type == "merge":
        ai = ai_value or {}
        op = operator_value or {}
        return bool(ai.get("split")) == bool(op.get("split")) and sorted(
            str(x) for x in (ai.get("merge_with") or [])
        ) == sorted(str(x) for x in (op.get("merge_with") or []))
    return False


# Движок, который классифицирует СЕЙЧАС. Облачная рутина (``routine``) выключена
# с 2026-08-12; её вердикты остаются в БД навсегда как Корпус, но метрику
# качества по ним нельзя выдавать за метрику живой системы.
LIVE_ENGINE = "headless"


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

    return {
        "total_classified": int(total_classified),
        "by_type": out,
        "live_engine": LIVE_ENGINE,
        "by_engine": await _agree_rate_by_engine(session),
        "classified_by_engine": await _classified_by_engine(session),
    }


async def _classified_by_engine(session) -> Dict[str, int]:
    """Сколько вердиктов вынес каждый движок. ``source`` — единственный различитель."""
    rows = (
        await session.execute(
            select(ContentClassification.source, func.count()).group_by(
                ContentClassification.source
            )
        )
    ).all()
    return {str(src or "unknown"): int(n or 0) for src, n in rows}


async def _agree_rate_by_engine(session) -> Dict[str, Dict[str, Any]]:
    """agree-rate отдельно по каждому движку.

    **Зачем разделение.** Сводная цифра складывает вердикты облачной рутины
    (06.07–12.08, 40 677 штук) и живого DeepSeek — а это разные системы. Разбор
    завала 2026-08-18 показал разрыв в порядок: ``hold`` 28.9% против 0.4%,
    расхождение на одинаковом тексте 34.9% против 4.5%. Сводный agree-rate по
    действию (55%) описывает движок, которого нет, и на панели читается как
    оценка работающего. Урок был записан в журнал дистилляций тем же днём —
    здесь он применён к метрике, а не только к чеканке правил.
    """
    rows = (
        await session.execute(
            select(
                ContentClassification.source,
                ClassificationCorrection.verdict_type,
                ClassificationCorrection.outcome,
                func.count(),
            )
            .join(
                ContentClassification,
                ContentClassification.id == ClassificationCorrection.classification_id,
            )
            .group_by(
                ContentClassification.source,
                ClassificationCorrection.verdict_type,
                ClassificationCorrection.outcome,
            )
        )
    ).all()

    per_engine: Dict[str, Dict[str, Dict[str, int]]] = {}
    for source, vtype, outcome, n in rows:
        if vtype not in VERDICT_TYPES or outcome not in ("agree", "correct"):
            continue
        engine = str(source or "unknown")
        bucket = per_engine.setdefault(
            engine, {t: {"agree": 0, "correct": 0} for t in VERDICT_TYPES}
        )
        bucket[vtype][outcome] = int(n or 0)

    out: Dict[str, Dict[str, Any]] = {}
    for engine, types in per_engine.items():
        out[engine] = {}
        for t in VERDICT_TYPES:
            a, c = types[t]["agree"], types[t]["correct"]
            total = a + c
            out[engine][t] = {
                "agree": a,
                "correct": c,
                "total": total,
                "agree_rate": round(a / total, 3) if total else None,
            }
    return out


async def _theme_usage_counts(session, *, verdict_rows: int = 5000) -> Dict[str, int]:
    """Частота использования тем в последних вердиктах (учитывает правки: тема в
    verdict уже актуальная после reassign). Python-подсчёт — портируемо PG/SQLite."""
    counts: Dict[str, int] = {}
    res = await session.execute(
        select(ContentClassification.verdict)
        .order_by(ContentClassification.id.desc())
        .limit(verdict_rows)
    )
    for (verdict,) in res.all():
        theme = str((verdict or {}).get("theme") or "").strip()
        if theme:
            counts[theme] = counts.get(theme, 0) + 1
    return counts


async def themes_list(session) -> List[Dict[str, Any]]:
    """Темы для UI: канонический словарь (миграция 069) + не-канонические остатки.

    Канон идёт первым (по position), у каждой темы счётчик использования в
    вердиктах. Остатки (темы, ещё встречающиеся в вердиктах, но не в словаре)
    отдаются с ``canon: false`` — редактор покажет их кандидатами на слияние.
    """
    canon_rows = (
        (await session.execute(select(ClassifierTheme).order_by(ClassifierTheme.position)))
        .scalars()
        .all()
    )
    counts = await _theme_usage_counts(session)
    out = [
        {
            "theme": t.name,
            "count": counts.pop(t.name, 0),
            "canon": True,
            "position": t.position,
            "description": t.description,
            "is_service": bool(t.is_service),
            # NUMERIC приезжает Decimal — JSON его не сериализует, а float здесь
            # безопасен: доли задаются человеком с шагом в процент.
            "share_percent": (None if t.share_percent is None else float(t.share_percent)),
        }
        for t in canon_rows
    ]
    leftovers = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    out.extend({"theme": t, "count": n, "canon": False} for t, n in leftovers)
    return out


async def add_theme(session, name: str) -> Dict[str, Any]:
    """Добавить тему в канонический словарь (редактор оператора)."""
    name = (name or "").strip()
    if not name:
        return {"ok": False, "error": "empty_name"}
    existing = (
        await session.execute(select(ClassifierTheme).where(ClassifierTheme.name == name))
    ).scalar_one_or_none()
    if existing:
        return {"ok": False, "error": "exists"}
    max_pos = (await session.execute(select(func.max(ClassifierTheme.position)))).scalar() or 0
    session.add(ClassifierTheme(name=name, position=max_pos + 1))
    await session.commit()
    return {"ok": True, "theme": name}


async def aliases_list(session) -> List[Dict[str, Any]]:
    """Синонимы тем для редактора: ``alias → canon`` + висячие каноны.

    ``canon_known: false`` означает, что тема-получатель удалена из словаря —
    FK у таблицы нет намеренно (запись вердикта не должна падать из-за правки
    в редакторе), поэтому висячий синоним чинится глазами оператора здесь.
    """
    canon_names = {
        str(n) for n in (await session.execute(select(ClassifierTheme.name))).scalars().all()
    }
    rows = (
        (await session.execute(select(ClassifierThemeAlias).order_by(ClassifierThemeAlias.alias)))
        .scalars()
        .all()
    )
    return [
        {"alias": r.alias, "canon": r.canon, "canon_known": r.canon in canon_names} for r in rows
    ]


async def add_alias(session, alias: str, canon: str) -> Dict[str, Any]:
    """Завести синоним темы (редактор оператора).

    Канон обязан существовать в словаре: синоним, ведущий в никуда, молча
    переписал бы тему на несуществующую — хуже, чем оставить исходное
    написание. Сам синоним каноном быть не может по той же причине, по которой
    канон побеждает синоним в ``_theme_canon_map``: иначе тема начала бы
    переписывать сама себя.
    """
    alias_key = (alias or "").strip().lower()
    canon = (canon or "").strip()
    if not alias_key or not canon:
        return {"ok": False, "error": "empty"}

    canon_names = {
        str(n) for n in (await session.execute(select(ClassifierTheme.name))).scalars().all()
    }
    if canon not in canon_names:
        return {"ok": False, "error": "unknown_canon"}
    if alias_key in {n.lower() for n in canon_names}:
        return {"ok": False, "error": "alias_is_canon"}

    existing = (
        await session.execute(
            select(ClassifierThemeAlias).where(ClassifierThemeAlias.alias == alias_key)
        )
    ).scalar_one_or_none()
    if existing:
        existing.canon = canon
        await session.commit()
        return {"ok": True, "alias": alias_key, "canon": canon, "updated": True}

    session.add(ClassifierThemeAlias(alias=alias_key, canon=canon))
    await session.commit()
    return {"ok": True, "alias": alias_key, "canon": canon, "updated": False}


async def delete_alias(session, alias: str) -> Dict[str, Any]:
    """Убрать синоним. Вердикты не трогает — они уже записаны каноном."""
    alias_key = (alias or "").strip().lower()
    if not alias_key:
        return {"ok": False, "error": "empty"}
    res = await session.execute(
        delete(ClassifierThemeAlias).where(ClassifierThemeAlias.alias == alias_key)
    )
    await session.commit()
    return {"ok": True, "alias": alias_key, "removed": int(res.rowcount or 0)}


async def canonicalize_existing(session) -> Dict[str, Any]:
    """Разово привести уже записанные вердикты к канону по текущему словарю.

    Нужна потому, что нормализация на запись чинит будущее, а 1992 вердикта с
    неканоническими темами уже лежат в Корпусе и продолжают дробить agree-rate
    и дистилляцию. Идемпотентна: повторный прогон ничего не меняет.

    Неизвестные написания **не трогает** — они возвращаются в ``unknown``,
    чтобы оператор завёл синоним осознанно, а не обнаружил подгонку постфактум.
    """
    canon_map = await _theme_canon_map(session)
    moved: Dict[str, int] = {}
    unknown: Dict[str, int] = {}

    rows = (await session.execute(select(ContentClassification))).scalars().all()
    for cls in rows:
        verdict = dict(cls.verdict or {})
        raw = str(verdict.get("theme") or "").strip()
        if not raw:
            continue
        canon = canon_map.get(raw.lower())
        if canon is None:
            unknown[raw] = unknown.get(raw, 0) + 1
            continue
        if canon != raw:
            verdict["theme"] = canon
            cls.verdict = verdict
            moved[f"{raw} → {canon}"] = moved.get(f"{raw} → {canon}", 0) + 1
    await session.commit()
    return {"moved": sum(moved.values()), "detail": moved, "unknown": unknown}


async def reassign_theme(session, old: str, new: str) -> int:
    """Перенести все вердикты с темой ``old`` на ``new`` (посты не теряются).

    Обновляет JSON-вердикты и правки оператора по теме. Возвращает число
    затронутых вердиктов. Python-обход вместо JSON-операторов СУБД —
    портируемо между Postgres и SQLite тестов; объёмы (тысячи строк) ок.
    """
    moved = 0
    rows = (await session.execute(select(ContentClassification))).scalars().all()
    for cls in rows:
        verdict = dict(cls.verdict or {})
        if str(verdict.get("theme") or "").strip() == old:
            verdict["theme"] = new
            cls.verdict = verdict
            moved += 1
    corr_rows = (
        (
            await session.execute(
                select(ClassificationCorrection).where(
                    ClassificationCorrection.verdict_type == "theme"
                )
            )
        )
        .scalars()
        .all()
    )
    for corr in corr_rows:
        if isinstance(corr.operator_value, str) and corr.operator_value.strip() == old:
            corr.operator_value = new
    await session.commit()
    return moved


async def delete_theme(session, name: str, reassign_to: str) -> Dict[str, Any]:
    """Удалить тему из словаря, перенеся её посты в ``reassign_to`` (заказ 2026-07-18)."""
    name = (name or "").strip()
    reassign_to = (reassign_to or "").strip()
    if not name or not reassign_to or name == reassign_to:
        return {"ok": False, "error": "bad_args"}
    target = (
        await session.execute(select(ClassifierTheme).where(ClassifierTheme.name == reassign_to))
    ).scalar_one_or_none()
    if target is None:
        return {"ok": False, "error": "target_not_in_dictionary"}
    moved = await reassign_theme(session, name, reassign_to)
    row = (
        await session.execute(select(ClassifierTheme).where(ClassifierTheme.name == name))
    ).scalar_one_or_none()
    if row is not None:
        # Доля наполнения переезжает вместе с постами, но только в пустое место.
        # Иначе удаление темы молча снимало бы потолок: посты продолжают идти под
        # именем получателя, а ограничения на них больше нет. Если у получателя
        # доля уже задана — она и остаётся: это явный выбор оператора, а
        # складывать два потолка бессмысленно.
        if target.share_percent is None and row.share_percent is not None:
            target.share_percent = row.share_percent
        await session.delete(row)
        await session.commit()
    return {"ok": True, "deleted": name, "reassign_to": reassign_to, "moved": moved}


# Через сколько часов без вердиктов считаем ИИ-фильтр молчащим. Cron прогонов —
# каждые 3 часа, так что 8 часов = два пропущенных прогона плюс запас. Тот же
# порог, что у сторожа ``modules.classifier.heartbeat``: страница и алёрт обязаны
# называть молчанием одно и то же, иначе человек видит зелёное, а телеграм красное.
STALE_AFTER_HOURS = 8.0


async def health_stats(session, *, days: int = DEFAULT_SOURCE_DAYS) -> Dict[str, Any]:
    """Диагностика работы рутины: успевает ли она за потоком (заказ 2026-07-16).

    - ``backlog`` — собранные в окне свежести посты БЕЗ вердикта (что рутина
      ещё не разобрала); по регионам и всего;
    - ``verdicts_24h`` — пропускная способность за сутки (по регионам);
    - ``duplicates_prevented`` не считаем отдельно: дедуп по ``lip`` в
      ``fetch_pending``/``record_verdicts`` исключает повторную трату токенов
      by construction (уже размеченный lip не выдаётся и не перезаписывается).
    """
    cutoff = datetime.utcnow() - timedelta(days=days)
    audit_rows = (
        await session.execute(
            select(CollectedPostAudit.region_code, CollectedPostAudit.lip).where(
                CollectedPostAudit.collected_at >= cutoff
            )
        )
    ).all()
    lips_by_region: Dict[str, set] = {}
    all_lips: set = set()
    for region, lip in audit_rows:
        lips_by_region.setdefault(region or "", set()).add(lip)
        all_lips.add(lip)

    classified: set = set()
    if all_lips:
        chunk = list(all_lips)
        for i in range(0, len(chunk), 5000):
            classified.update(
                lip
                for (lip,) in (
                    await session.execute(
                        select(ContentClassification.lip).where(
                            ContentClassification.lip.in_(chunk[i : i + 5000])
                        )
                    )
                ).all()
            )

    day_ago = datetime.utcnow() - timedelta(hours=24)
    verdict_rows = (
        await session.execute(
            select(ContentClassification.region_code, func.count())
            .where(ContentClassification.created_at >= day_ago)
            .group_by(ContentClassification.region_code)
        )
    ).all()
    last_verdict_at = (
        await session.execute(select(func.max(ContentClassification.created_at)))
    ).scalar()

    backlog_by_region = {r: len(lips - classified) for r, lips in sorted(lips_by_region.items())}
    collected_total = len(all_lips)
    backlog_total = len(all_lips - classified)
    return {
        "window_days": days,
        "collected_in_window": collected_total,
        "classified_in_window": collected_total - backlog_total,
        "backlog": backlog_total,
        "backlog_by_region": {r: n for r, n in backlog_by_region.items() if n},
        "verdicts_24h": int(sum(n for _, n in verdict_rows)),
        "verdicts_24h_by_region": {r or "": int(n) for r, n in sorted(verdict_rows)},
        "last_verdict_at": last_verdict_at.isoformat() if last_verdict_at else None,
        # Возраст следа работы в часах — считаем здесь, а не в браузере: страница
        # получала «последний вердикт: 16.08» и молча его рисовала, а человеку
        # приходилось самому вычитать даты, чтобы понять, что движок стоит трое
        # суток (инцидент 2026-08-19).
        "last_verdict_age_hours": (
            round(max(0.0, (datetime.utcnow() - last_verdict_at).total_seconds() / 3600.0), 1)
            if last_verdict_at
            else None
        ),
        "stale_after_hours": STALE_AFTER_HOURS,
        "stale": bool(
            last_verdict_at
            and (datetime.utcnow() - last_verdict_at).total_seconds() > STALE_AFTER_HOURS * 3600
        ),
        "coverage_pct": (
            round(100.0 * (collected_total - backlog_total) / collected_total, 1)
            if collected_total
            else None
        ),
    }


# ═══════════════════ Воронка, прогресс оператора, аранжировка ═══════════════
# Заказ владельца 2026-08-19. Три вопроса, на которые панель не отвечала:
# доходит ли собранное до движка, насколько оператор отстал и как разбирать
# ленту пачками. Агрегация делается в Python, а не в SQL: тот же приём, что в
# ``_theme_usage_counts`` — ``verdict`` лежит в JSON, и операторы jsonb развели
# бы прод (PostgreSQL) с тестами (SQLite).


async def funnel_stats(session, *, hours: int = 24) -> Dict[str, Any]:
    """Сквозная воронка за окно: собрано → размечено → исход → в публикации.

    Отсев дублями сознательно не считаем: ``record_collection_audit``
    идемпотентен по ``lip`` (first-seen wins), повторно пришедший пост новой
    строки не создаёт — честной цифры в базе нет. Нарисовать неизмеримое хуже,
    чем не рисовать: панель уже один раз научила смотреть мимо, когда возраст
    последнего вердикта лежал серой подписью (инцидент 2026-08-19).
    """
    cutoff = datetime.utcnow() - timedelta(hours=hours)

    collected_rows = (
        await session.execute(
            select(CollectedPostAudit.lip, CollectedPostAudit.region_code).where(
                CollectedPostAudit.collected_at >= cutoff
            )
        )
    ).all()
    collected_lips = {lip for lip, _ in collected_rows}
    collected_regions = {r for _, r in collected_rows if r}

    verdict_rows = (
        await session.execute(
            select(
                ContentClassification.lip,
                ContentClassification.verdict,
            ).where(ContentClassification.created_at >= cutoff)
        )
    ).all()

    actions: Dict[str, int] = {"publish": 0, "delete": 0, "hold": 0}
    classified_lips: set = set()
    for lip, verdict in verdict_rows:
        classified_lips.add(lip)
        action = str((verdict or {}).get("action") or "").strip().lower()
        if action in actions:
            actions[action] += 1

    published_lips, published_regions = await _published_lips(session, cutoff=cutoff)

    collected_total = len(collected_lips)
    # Размеченным считаем пересечение: вердикт мог приехать по посту, собранному
    # ДО окна (движок доедает хвост), и тогда «размечено больше, чем собрано»
    # выглядело бы поломкой счётчика.
    classified_in_window = len(collected_lips & classified_lips)
    return {
        "window_hours": hours,
        "collected": collected_total,
        "classified": classified_in_window,
        "unclassified": collected_total - classified_in_window,
        "verdicts": len(verdict_rows),
        "publish": actions["publish"],
        "delete": actions["delete"],
        "hold": actions["hold"],
        "published": len(classified_lips & published_lips),
        # Охват журнала публикаций. На проде он ведётся по ОДНОМУ району из 29
        # собираемых, и «дошло до читателя» без этой пары читается как цифра по
        # всей сети — заниженная величина с уверенной подписью. Расход токенов
        # по той же причине убран вовсе: ``tokens_estimate`` не заполняет никто
        # (0 из 2238 вердиктов за сутки), и панель рисовала бы «токенов: 0» там,
        # где движок потратил миллионы.
        "published_regions": len(published_regions),
        "collected_regions": len(collected_regions),
        "coverage_pct": (
            round(100.0 * classified_in_window / collected_total, 1) if collected_total else None
        ),
    }


async def _published_lips(session, *, cutoff: datetime) -> tuple:
    """lip, дошедшие до читателя, и районы, по которым журнал вообще ведётся.

    ``published_post_id is not None`` — единственный признак, что свод­ка
    опубликована. Без него в «дошло до читателя» попали бы кандидаты свод­ок,
    которые так и остались в очереди.

    Второе значение — охват. Журнал курации ведётся не по всей сети (на
    2026-08-19 — один район из 29 собираемых), и число без охвата обещает
    больше, чем измеряет.
    """
    runs = (
        (
            await session.execute(
                select(BulletinCurationRun).where(
                    BulletinCurationRun.created_at >= cutoff,
                    BulletinCurationRun.published_post_id.isnot(None),
                )
            )
        )
        .scalars()
        .all()
    )
    out: set = set()
    regions: set = set()
    for run in runs:
        if run.region_code:
            regions.add(run.region_code)
        cands = run.candidates or []
        if not isinstance(cands, (list, tuple)):
            continue
        for c in cands:
            if isinstance(c, dict):
                lip = str(c.get("lip") or "").strip()
                if lip:
                    out.add(lip)
    return out, regions


async def operator_progress_stats(session, *, hours: int = 24) -> Dict[str, Any]:
    """Насколько оператор отстаёт от движка.

    **Темп считается по нажатиям (``classification_corrections``), а не по
    ``reviewed_at``.** Архивация завала 2026-08-18 проставила ``reviewed_at``
    44 177 постам одним ``UPDATE``; по нему темп вышел бы фантастическим, а
    отставание — нулевым, хотя оператор не нажал ни разу.
    """
    cutoff = datetime.utcnow() - timedelta(hours=hours)

    engine_verdicts = int(
        (
            await session.execute(
                select(func.count(ContentClassification.id)).where(
                    ContentClassification.created_at >= cutoff
                )
            )
        ).scalar()
        or 0
    )
    # Один пост = одна разобранная карточка, сколько бы типов вердикта оператор
    # ни тронул: считаем различные classification_id, иначе смена темы плюс
    # согласие с действием выглядели бы двумя разобранными постами.
    reviewed = int(
        (
            await session.execute(
                select(func.count(func.distinct(ClassificationCorrection.classification_id))).where(
                    ClassificationCorrection.created_at >= cutoff
                )
            )
        ).scalar()
        or 0
    )
    remaining = int(
        (
            await session.execute(
                select(func.count(ContentClassification.id)).where(
                    ContentClassification.reviewed_at.is_(None)
                )
            )
        ).scalar()
        or 0
    )

    rate = round(reviewed / hours, 1) if reviewed else None
    return {
        "window_hours": hours,
        "engine_verdicts": engine_verdicts,
        "operator_reviewed": reviewed,
        "remaining": remaining,
        "operator_rate_per_hour": rate,
        # Часов работы оператора, чтобы разгрести очередь текущим темпом. Без
        # нажатий темп не выдумываем: ноль в знаменателе дал бы бесконечность,
        # а «0 ч» соврал бы, что отставания нет.
        "lag_hours": round(remaining / rate, 1) if rate else None,
    }


def _normalized_text(text: Any) -> str:
    """Ключ дословного дубля: регистр и пробельные последовательности не считаются."""
    return " ".join(str(text or "").lower().split())


async def review_feed_grouped(
    session,
    *,
    region_code: Optional[str] = None,
    limit: int = 5000,
    cards_per_block: int = 40,
) -> List[Dict[str, Any]]:
    """Лента блоками «вердикт × тема», внутри блока дословные дубли схлопнуты.

    **Счётчики считаются по всей очереди, а рисуется верхушка блока.** Это
    разные числа, и путать их нельзя: при очереди в две тысячи постов лимит в
    пятьсот давал заголовок «мусор — 34» там, где мусора полторы сотни, а
    кнопка «Согласен со всем блоком» закрывала показанное вместо обещанного.
    Поэтому ``limit`` ограничивает выборку целиком (защита от неограниченного
    запроса), ``cards_per_block`` — только число нарисованных карточек, а
    ``total``/``ids`` остаются полными.
    """
    items = await review_feed(session, region_code=region_code, only_unreviewed=True, limit=limit)

    blocks: Dict[tuple, Dict[str, Any]] = {}
    for item in items:
        verdict = item.get("verdict") or {}
        action = str(verdict.get("action") or "?").strip().lower()
        theme = str(verdict.get("theme") or "?").strip()
        block = blocks.setdefault(
            (action, theme),
            {"action": action, "theme": theme, "total": 0, "cards": [], "_by_text": {}},
        )
        block["total"] += 1

        norm = _normalized_text(item.get("post_text"))
        # Посты без текста не группируем: пустая строка склеила бы их все в одну
        # фиктивную группу «одинаковых», хотя общего у них ровно ничего.
        existing = block["_by_text"].get(norm) if norm else None
        if existing is None:
            card = dict(item)
            card["duplicate_count"] = 1
            card["duplicate_ids"] = [item["id"]]
            card["duplicate_lips"] = [item["lip"]]
            block["cards"].append(card)
            if norm:
                block["_by_text"][norm] = card
        else:
            existing["duplicate_count"] += 1
            existing["duplicate_ids"].append(item["id"])
            existing["duplicate_lips"].append(item["lip"])

    out = []
    for block in blocks.values():
        block.pop("_by_text", None)
        # ids собираются ДО обрезки карточек: групповое действие обещает закрыть
        # весь блок, и обязано закрыть весь блок, а не его видимую верхушку.
        block["ids"] = [i for c in block["cards"] for i in c["duplicate_ids"]]
        all_cards = block["cards"]
        block["cards"] = all_cards[:cards_per_block]
        block["hidden_cards"] = len(all_cards) - len(block["cards"])
        out.append(block)
    # Крупные блоки первыми: пачка на 267 карточек экономит больше нажатий, чем
    # блок на три, и должна попадаться оператору раньше.
    out.sort(key=lambda b: b["total"], reverse=True)
    return out


async def bulk_agree(session, classification_ids: Sequence[int]) -> Dict[str, Any]:
    """✅ «Согласен со всей группой»: ``agree_all`` по каждому id пачки.

    Идёт через ``agree_all``, а не через прямой ``UPDATE reviewed_at``: реакции
    обязаны попасть в ``classification_corrections``, иначе пачка разбирается
    вхолостую — agree-rate её не увидит, и дистилляция не получит сырья. Ровно
    так завал 2026-08-18 дал ноль коррекций на 44 177 архивированных строк.
    """
    finalized = 0
    missing = 0
    for cid in classification_ids:
        res = await agree_all(session, int(cid))
        if res.get("ok"):
            finalized += 1
        else:
            missing += 1
    return {"ok": True, "finalized": finalized, "missing": missing}


async def bulk_correct(
    session,
    classification_ids: Sequence[int],
    *,
    verdict_type: str,
    operator_value: Any,
) -> Dict[str, Any]:
    """Правка одного аспекта вердикта сразу по группе + закрытие карточек.

    **Финализирует, в отличие от одиночного ``correct``.** Карточка-дубль
    показывает один пост, но представляет всю группу дословно одинаковых:
    решение оператора относится к ним всем сразу, и требовать отдельное
    «Готово» на каждую из четырёх десятков штук — ровно та работа, ради
    устранения которой группировка и делалась.

    Сам вердикт не переписывается (как и в ``correct``): расхождение «сеть
    сказала так, оператор поправил эдак» — это и есть обучающий материал,
    ``enforce`` накладывает правку поверх при публикации.
    """
    if verdict_type not in VERDICT_TYPES:
        return {"ok": False, "corrected": 0, "error": f"unknown verdict_type: {verdict_type}"}

    corrected = 0
    missing = 0
    for cid in classification_ids:
        res = await correct(
            session,
            int(cid),
            verdict_type=verdict_type,
            operator_value=operator_value,
        )
        if not res.get("ok"):
            missing += 1
            continue
        # Остальные аспекты вердикта закрываем как согласие — тот же смысл, что
        # у кнопки «Готово»: поправлено то, что назвали, прочее принято.
        await finalize(session, int(cid))
        corrected += 1
    return {"ok": True, "corrected": corrected, "missing": missing}


# Сколько непроверенных строк тянем в пул перед случайным выбором темы.
# Пул нужен, чтобы тема выбиралась из ТОГО, что реально есть в очереди, а не из
# справочника тем: справочник знает «православие», которого в завале может не
# быть ни одного поста, и оператор получал бы пустые выдачи. Полторы тысячи —
# компромисс: на завале в 44 тысячи это заметная доля тем, а сортировка
# random() по такому объёму стоит десятки миллисекунд.
RANDOM_REVIEW_POOL = 1500


async def review_feed_random(
    session,
    *,
    limit: int = 10,
    exclude_ids: Optional[Sequence[int]] = None,
) -> Dict[str, Any]:
    """Случайная пачка непроверенных постов ОДНОЙ случайной темы (заказ владельца 2026-08-20).

    Зачем отдельно от ``review_feed_grouped``. Та лента ходит по очереди сверху
    вниз, крупными блоками, и оператор неделями разбирает один и тот же пласт:
    свежие вердикты одной темы вытесняют остальные, а завал в других темах
    никто не видит. Дистилляция 2026-08-18 напоролась ровно на это — «мерили
    покойника», потому что сырьё приходило из одного угла потока.

    Что делает: берёт случайный пул непроверенных вердиктов **по всем районам**,
    смотрит, какие темы в нём реально есть, выбирает одну случайно и отдаёт из
    неё до ``limit`` случайных постов. Тема одна на выдачу сознательно — так
    оператор держит в голове один критерий, а не переключается между «мусором» и
    «происшествиями» на каждой карточке.

    Форма ответа — тот же блок, что у ``review_feed_grouped`` (``action``,
    ``theme``, ``total``, ``ids``, ``cards``): панель рисует и групповые кнопки
    применяет одним и тем же кодом. Разница внутри блока одна — здесь он
    собирается по теме, а не по паре «действие × тема», потому что владелец
    ориентируется по теме, а действия внутри пачки как раз и надо сравнить.

    ``exclude_ids`` — что уже показано в этом заходе: кнопка «дать ещё 10» не
    должна возвращать те же карточки, пока оператор их не закрыл.
    """
    import random

    excluded = {int(i) for i in (exclude_ids or [])}

    stmt = (
        select(ContentClassification)
        .where(ContentClassification.reviewed_at.is_(None))
        .order_by(func.random())
        .limit(RANDOM_REVIEW_POOL)
    )
    rows = [r for r in (await session.execute(stmt)).scalars().all() if r.id not in excluded]
    if not rows:
        return {"blocks": [], "count": 0, "theme": None, "themes_available": 0}

    canon_map = await _theme_canon_map(session)

    by_theme: Dict[str, List[ContentClassification]] = {}
    for row in rows:
        verdict = row.verdict if isinstance(row.verdict, dict) else {}
        theme = canonicalize_theme(verdict.get("theme"), canon_map) or "—"
        by_theme.setdefault(theme, []).append(row)

    # Тема выбирается равновероятно среди присутствующих, а НЕ пропорционально
    # объёму: иначе «мусор», которого в завале больше всего, выпадал бы почти
    # всегда, и редкие темы оператор не увидел бы никогда — то самое, от чего
    # эта выдача и лечит.
    theme = random.choice(sorted(by_theme))
    picked = by_theme[theme]
    random.shuffle(picked)
    picked = picked[:limit]

    items = await _attach_filter_decision(session, picked)

    # Дословные дубли схлопываются так же, как в блочной ленте: пачка из десяти
    # копий одного текста — это одно решение оператора, а не десять.
    cards: List[Dict[str, Any]] = []
    by_text: Dict[str, Dict[str, Any]] = {}
    for item in items:
        norm = _normalized_text(item.get("post_text"))
        existing = by_text.get(norm) if norm else None
        if existing is None:
            card = dict(item)
            card["duplicate_count"] = 1
            card["duplicate_ids"] = [item["id"]]
            card["duplicate_lips"] = [item["lip"]]
            cards.append(card)
            if norm:
                by_text[norm] = card
        else:
            existing["duplicate_count"] += 1
            existing["duplicate_ids"].append(item["id"])
            existing["duplicate_lips"].append(item["lip"])

    block = {
        # Действия внутри пачки разные (в этом и смысл случайной выборки),
        # поэтому в заголовке блока — тема, а поле action пустое. Панель
        # показывает действие на каждой карточке отдельно.
        "action": "",
        "theme": theme,
        "total": sum(c["duplicate_count"] for c in cards),
        "ids": [i for c in cards for i in c["duplicate_ids"]],
        "cards": cards,
        "hidden_cards": 0,
        "random": True,
    }
    return {
        "blocks": [block],
        "count": block["total"],
        "theme": theme,
        "themes_available": len(by_theme),
    }
