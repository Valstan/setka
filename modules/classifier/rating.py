"""Витрина топ-N по рейтингу — измерение, а не отбор (звено 5, шаг 1).

**Ничего не публикует и ни на что не влияет.** Задача одна: показать
владельцу, как выглядит верхушка при разных значениях ``alpha``, чтобы он
выбрал одно глазами на боевых постах. Переключение публикации на рейтинг —
следующий заход, и до него порядок жёсткий: рейтинг ДО снятия фильтров.

Топ считается ВНУТРИ разрешённых вердиктом (``selection.fetch_publish_lips``)
— иначе витрина показывала бы то, что нейро-фильтр уже отверг.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from modules.classifier.audit_window import AUDIT_WINDOW_HOURS, in_window, window_cutoff
from utils.post_utils import post_rating


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

    Окно — то же, что у обновления метрик (``audit_window``): показывать надо
    то, из чего реально можно выбрать, и ровно то, что таска меряет.
    """
    from sqlalchemy import select

    from config.classifier import get_rating_views_alpha
    from database.models_extended import CollectedPostAudit
    from modules.classifier import selection

    if alphas is None:
        configured = get_rating_views_alpha()
        # dict.fromkeys, а не set: порядок колонок на витрине задан, а
        # настроенная alpha может совпасть с опорной — дубля быть не должно.
        alphas = list(dict.fromkeys([configured, 0.5, 0.0]))

    cutoff = window_cutoff(AUDIT_WINDOW_HOURS)
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
        in_window(cutoff),
    )
    if theme:
        stmt = stmt.where(CollectedPostAudit.theme == theme)

    allowed = await selection.fetch_publish_lips(session, region_code)
    rows: List[Dict[str, Any]] = []
    in_window_rows = 0
    for r in (await session.execute(stmt)).all():
        in_window_rows += 1
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
        # Сколько строк аудита вообще попало в окно — ДО пересечения с
        # вердиктом. Без этого числа «кандидатов: 0» неотличимо от двух разных
        # миров: «в районе тихо» и «вердиктов нет». Второе — не редкость и не
        # авария кода: fetch_publish_lips по контракту возвращает ПУСТОЙ набор
        # и при ошибке чтения (сознательный fail-closed, см. selection.py), и
        # когда классификатор по району просто не отрабатывал. По этой витрине
        # владелец выбирает показатель степени формулы — молча показывать ему
        # три пустые колонки нельзя.
        "in_window": in_window_rows,
        "candidates": len(rows),
        # Охват источника рядом с числом — правило после #493: у каждой цифры
        # на панели спрашивать, какую долю реальности покрывает её источник.
        "measured": measured,
        "dropped_by_filters": dropped_by_filters,
        "alphas": {str(a): rank_rows(rows, alpha=a, n=n) for a in alphas},
    }
