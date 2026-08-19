"""Витрина топ-N по рейтингу — измерение, а не отбор (звено 5, шаг 1).

**Ничего не публикует и ни на что не влияет.** Задача одна: показать
владельцу, как выглядит верхушка при разных значениях ``alpha``, чтобы он
выбрал одно глазами на боевых постах. Переключение публикации на рейтинг —
следующий заход, и до него порядок жёсткий: рейтинг ДО снятия фильтров.

Топ считается ВНУТРИ разрешённых вердиктом (``selection.fetch_publish_lips``)
— иначе витрина показывала бы то, что нейро-фильтр уже отверг.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence

from utils.post_utils import post_rating

WINDOW_HOURS = 72


def rank_rows(rows: Sequence[Dict[str, Any]], *, alpha: float, n: int) -> List[Dict[str, Any]]:
    """Проставить ``score`` и вернуть верхушку из ``n`` строк.

    Посты без измеренных просмотров (``score is None``) уходят в хвост, а не
    наверх: их балл неизвестен, и делать вид, что он нулевой или высокий,
    одинаково неправда.
    """
    scored = []
    for row in rows:
        item = dict(row)
        item["score"] = post_rating(
            row.get("views"),
            row.get("likes"),
            row.get("comments"),
            row.get("reposts"),
            alpha=alpha,
        )
        scored.append(item)
    scored.sort(key=lambda r: (r["score"] is not None, r["score"] or 0.0), reverse=True)
    return scored[:n]


async def top_by_rating(
    session,
    *,
    region_code: str,
    theme: Optional[str] = None,
    n: int = 10,
    alphas: Optional[Sequence[float]] = None,
) -> Dict[str, Any]:
    """Верхушка рейтинга района по каждому из ``alphas``.

    По умолчанию первой идёт НАСТРОЕННАЯ alpha (``RATING_VIEWS_ALPHA``), а не
    жёсткая 0.25: витрина существует ради выбора этого значения и обязана
    показывать то, что реально стоит в конфиге. Рядом — две опорные точки:
    0.5 (как сортируется лента сейчас) и 0 (чистый охват).

    Окно — те же 72 часа, что у отбора: показывать надо то, из чего реально
    можно выбрать.
    """
    from sqlalchemy import or_, select

    from config.classifier import get_rating_views_alpha
    from database.models_extended import CollectedPostAudit
    from modules.classifier import selection

    if alphas is None:
        configured = get_rating_views_alpha()
        # dict.fromkeys, а не set: порядок колонок на витрине задан, а
        # настроенная alpha может совпасть с опорной — дубля быть не должно.
        alphas = list(dict.fromkeys([configured, 0.5, 0.0]))

    cutoff = datetime.utcnow() - timedelta(hours=WINDOW_HOURS)
    stmt = select(
        CollectedPostAudit.lip,
        CollectedPostAudit.post_url,
        CollectedPostAudit.post_text,
        CollectedPostAudit.theme,
        CollectedPostAudit.decision,
        CollectedPostAudit.views,
        CollectedPostAudit.likes,
        CollectedPostAudit.comments,
        CollectedPostAudit.reposts,
        CollectedPostAudit.published_at,
        CollectedPostAudit.metrics_updated_at,
    ).where(
        CollectedPostAudit.region_code == region_code,
        or_(
            CollectedPostAudit.published_at > cutoff,
            (CollectedPostAudit.published_at.is_(None))
            & (CollectedPostAudit.collected_at > cutoff),
        ),
    )
    if theme:
        stmt = stmt.where(CollectedPostAudit.theme == theme)

    allowed = await selection.fetch_publish_lips(session, region_code)
    rows: List[Dict[str, Any]] = []
    for r in (await session.execute(stmt)).all():
        if r.lip not in allowed:
            continue
        rows.append(
            {
                "lip": r.lip,
                "post_url": r.post_url,
                "post_text": (r.post_text or "")[:300],
                "theme": r.theme,
                # decision алгоритмического фильтра (kept|dropped) — витрина
                # НЕ фильтрует по нему намеренно (D-024: алгоритмы отсеивают
                # 43% постов, которые ИИ считает публикуемыми), но обязана
                # показывать его, а не выдавать чужой отсев за сегодняшний
                # публикуемый набор.
                "decision": r.decision,
                "views": r.views,
                "likes": r.likes,
                "comments": r.comments,
                "reposts": r.reposts,
                "published_at": r.published_at.isoformat() if r.published_at else None,
                "metrics_updated_at": (
                    r.metrics_updated_at.isoformat() if r.metrics_updated_at else None
                ),
            }
        )

    measured = sum(1 for r in rows if r["views"] is not None)
    # Витрина считает топ ВНУТРИ разрешённых вердиктом lip'ов, а не внутри
    # сегодняшнего публикуемого набора — decision алгоритмического фильтра не
    # фильтруется (см. docstring модуля и D-024). Молча это вводит в
    # заблуждение: без разбивки владелец решит, что видит уже публикуемое.
    # Правило после разбора панели (#495): у каждой цифры спрашивать не
    # значение, а какую долю реальности покрывает её источник.
    dropped_by_filters = sum(1 for r in rows if r["decision"] == "dropped")
    return {
        "region": region_code,
        "theme": theme or "",
        "candidates": len(rows),
        # Охват источника рядом с числом — правило после #493: у каждой цифры
        # на панели спрашивать, какую долю реальности покрывает её источник.
        "measured": measured,
        "dropped_by_filters": dropped_by_filters,
        "alphas": {str(a): rank_rows(rows, alpha=a, n=n) for a in alphas},
    }
