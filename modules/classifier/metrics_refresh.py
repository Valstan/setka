"""Обновление метрик собранных постов — данные под рейтинг (звено 5, шаг 1).

Метрики в момент сбора почти нулевые: пост собирается через минуты после
публикации, и лайков у него ещё нет. Поэтому рейтинг строится не на том, что
видел сбор, а на том, что доросло за окно отсева.

**Границы прохода — правила владельца, не оптимизация:**

* **не трогаем посты старше 72 часов** — они всё равно отсеются по старости,
  и тратить на них вызовы ВК незачем;
* **не трогаем уже опубликованное нами** (``work_tables.lip``) — их рейтинг
  ни на что не влияет, пост из мешка уже ушёл;
* **берём обе стороны аудита, ``kept`` и ``dropped``.** Без метрик на
  отсеянных нельзя проверить находку D-024 (ИИ считает публикуемыми 43% того,
  что выкинули алгоритмы), а именно на неё опирается будущее снятие фильтров.

Объём посчитан на проде 2026-08-19: окно 72 часа = 7774 строки по 29 регионам,
то есть 78 батчей за круг и ~620 вызовов в сутки при прогоне раз в 3 часа.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

logger = logging.getLogger(__name__)

Ref = Tuple[int, int]

_WALL_RE = re.compile(r"wall(-?\d+)_(\d+)\s*$")

REFRESH_WINDOW_HOURS = 72


def ref_from_post_url(url: Optional[str]) -> Optional[Ref]:
    """``https://vk.com/wall-100_7`` → ``(-100, 7)``.

    ``lip`` для этого не годится: он хранится как ``{abs(owner_id)}_{id}`` и
    знак владельца теряет, а ``wall.getById`` без знака отдаст чужой пост или
    ничего. В ``post_url`` знак сохранён — берём оттуда.
    """
    if not url:
        return None
    m = _WALL_RE.search(str(url))
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)))


def drop_already_published(
    candidates: Sequence[Tuple[Ref, str]],
    published_lips: Set[str],
) -> List[Tuple[Ref, str]]:
    """Выкинуть посты, которые мы уже опубликовали. Чистая функция."""
    if not published_lips:
        return list(candidates)
    return [(ref, lip) for ref, lip in candidates if lip not in published_lips]


async def load_published_lips(session) -> Set[str]:
    """Все lip'ы, опубликованные нами, из ``work_tables.lip`` (JSON-списки)."""
    from sqlalchemy import select

    from database.models_extended import WorkTable

    out: Set[str] = set()
    rows = (await session.execute(select(WorkTable.lip))).all()
    for (lips,) in rows:
        for lip in lips or []:
            out.add(str(lip))
    return out


async def select_refresh_candidates(
    session,
    *,
    hours: int = REFRESH_WINDOW_HOURS,
    limit: int = 0,
) -> List[Tuple[Ref, str]]:
    """Посты аудита в окне ``hours``, пригодные для обновления метрик.

    Окно считается по ``published_at`` (возраст поста). Строки, где она ещё
    ``NULL`` — наследие до миграции 080 — добираются по ``collected_at``:
    ситуация одноразовая, таска сама проставит дату из ответа ВК.
    """
    from sqlalchemy import or_, select

    from database.models_extended import CollectedPostAudit

    cutoff = datetime.utcnow() - timedelta(hours=hours)
    stmt = (
        select(CollectedPostAudit.post_url, CollectedPostAudit.lip)
        .where(
            or_(
                CollectedPostAudit.published_at > cutoff,
                (CollectedPostAudit.published_at.is_(None))
                & (CollectedPostAudit.collected_at > cutoff),
            )
        )
        .order_by(CollectedPostAudit.collected_at.desc())
    )
    if limit:
        stmt = stmt.limit(limit)

    out: List[Tuple[Ref, str]] = []
    for url, lip in (await session.execute(stmt)).all():
        ref = ref_from_post_url(url)
        if ref is not None:
            out.append((ref, lip))
    return out


async def apply_metrics(
    session, metrics_by_ref: Dict[Ref, Dict[str, Any]], lip_by_ref: Dict[Ref, str]
) -> int:
    """Записать метрики в аудит. Возвращает число обновлённых строк.

    ``published_at`` перезаписывается только когда его ещё нет: дата поста не
    меняется, а ответ ВК может её и не принести.
    """
    from sqlalchemy import update

    from database.models_extended import CollectedPostAudit

    now = datetime.utcnow()
    updated = 0
    for ref, m in metrics_by_ref.items():
        lip = lip_by_ref.get(ref)
        if not lip:
            continue
        values: Dict[str, Any] = {
            "views": m.get("views"),
            "likes": m.get("likes"),
            "comments": m.get("comments"),
            "reposts": m.get("reposts"),
            "metrics_updated_at": now,
        }
        stmt = update(CollectedPostAudit).where(CollectedPostAudit.lip == lip)
        if m.get("published_at"):
            # Только если даты ещё нет — она не меняется со временем.
            await session.execute(
                stmt.where(CollectedPostAudit.published_at.is_(None)).values(
                    published_at=m["published_at"], **values
                )
            )
            await session.execute(
                stmt.where(CollectedPostAudit.published_at.isnot(None)).values(**values)
            )
        else:
            await session.execute(stmt.values(**values))
        updated += 1
    await session.commit()
    return updated


async def refresh_metrics(session, *, hours: int = REFRESH_WINDOW_HOURS) -> Dict[str, Any]:
    """Один круг обновления метрик. Никогда не бросает наружу."""
    import vk_api

    from modules.vk_monitor.post_metrics import fetch_metrics_for_token
    from modules.vk_token_router import get_healthy_read_token

    candidates = await select_refresh_candidates(session, hours=hours)
    published = await load_published_lips(session)
    live = drop_already_published(candidates, published)
    skipped = len(candidates) - len(live)
    if not live:
        return {"ok": True, "checked": 0, "updated": 0, "skipped_published": skipped}

    token = await get_healthy_read_token()
    if not token:
        # Молчать нельзя: без токена метрики не обновятся ни разу, а рейтинг
        # тихо застынет на старых числах.
        logger.warning("refresh_metrics: живого READ-токена нет, круг пропущен")
        return {
            "ok": False,
            "error": "no_read_token",
            "checked": len(live),
            "updated": 0,
            "skipped_published": skipped,
        }

    api = vk_api.VkApi(token=token).get_api()
    lip_by_ref = {ref: lip for ref, lip in live}
    metrics = fetch_metrics_for_token(api, [ref for ref, _ in live])
    updated = await apply_metrics(session, metrics, lip_by_ref)
    return {"ok": True, "checked": len(live), "updated": updated, "skipped_published": skipped}
