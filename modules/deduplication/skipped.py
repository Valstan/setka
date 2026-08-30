"""Журнал отсеянных дублей: кого признали повтором и больше не берём.

Заказ владельца 2026-08-30. Раньше дедуп работал так: из двух похожих постов
побеждал тот, кого случайно просмотрели первым (сообщества тасовались
``random.shuffle``), а проигравший исчезал бесследно. Через пару часов следующая
волна брала его как свежий кандидат — конкурента рядом уже нет, текст переписан,
фото перезалито, — и он выходил в ленту. Жалоба «одна новость выходит дважды»
описывала именно это: дубль не устранялся, а сдвигался во времени.

Теперь первоисточником считается тот, кто **вышел раньше** (решение владельца:
«кто раньше, тот и первоисточник»), а проигравший записывается сюда и в
кандидаты больше не попадает.

**Отдельно от work_tables.lip — по прямому возражению владельца.** Тот курсор
означает «опубликовано» и питает статистику; складывать в него неопубликованное
значило бы ей врать. Плюс его потолок 1000 записей вытеснял бы историю публикаций.

**Пост, который просто не влез в сводку, сюда НЕ попадает.** Он не дубль, и у
него должен остаться шанс выйти следующей волной, пока не кончилось окно
свежести — это тоже прямое требование владельца.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

logger = logging.getLogger(__name__)


async def fetch_skipped_lips(session, region_code: str) -> Set[str]:
    """lip'ы, уже отсеянные как дубли в этом регионе.

    Подмешиваются к курсору дедупа волны, поэтому отказ чтения обязан быть
    безобидным: пустое множество означает «ничего не знаем», и волна работает как
    до этой правки. Fail-closed здесь был бы хуже — он остановил бы публикацию
    целиком из-за вспомогательного журнала.
    """
    from sqlalchemy import select

    from database.models_extended import SkippedDuplicate

    try:
        rows = (
            await session.execute(
                select(SkippedDuplicate.lip).where(SkippedDuplicate.region_code == region_code)
            )
        ).all()
        return {str(lip) for (lip,) in rows if lip}
    except Exception as e:  # noqa: BLE001 — журнал не важнее волны
        logger.warning("skipped duplicates: чтение не удалось (%s): %s", region_code, e)
        return set()


async def record_skipped(
    session,
    *,
    region_code: str,
    wave_theme: Optional[str],
    entries: Sequence[Dict[str, Any]],
) -> int:
    """Записать отсеянные дубли. Возвращает число новых записей.

    ``entries`` — последовательность ``{"lip": ..., "original_lip": ..., "reason": ...}``.
    Повторная запись того же lip'а игнорируется: уникальный индекс по
    ``(region_code, lip)`` есть, но полагаться на исключение дорого — проверяем
    заранее одним запросом.
    """
    from sqlalchemy import select

    from database.models_extended import SkippedDuplicate

    try:
        fresh = [e for e in entries if str(e.get("lip") or "").strip()]
        if not fresh:
            return 0
        lips = [str(e["lip"]) for e in fresh]
        known = {
            str(lip)
            for (lip,) in (
                await session.execute(
                    select(SkippedDuplicate.lip).where(
                        SkippedDuplicate.region_code == region_code,
                        SkippedDuplicate.lip.in_(lips),
                    )
                )
            ).all()
        }
        added = 0
        seen: Set[str] = set()
        for entry in fresh:
            lip = str(entry["lip"])
            if lip in known or lip in seen:
                continue
            seen.add(lip)
            session.add(
                SkippedDuplicate(
                    lip=lip,
                    region_code=region_code,
                    original_lip=(entry.get("original_lip") or None),
                    reason=str(entry.get("reason") or "duplicate")[:32],
                    wave_theme=wave_theme,
                )
            )
            added += 1
        if added:
            await session.commit()
            logger.info(
                "skipped duplicates: записано %d (регион=%s тема=%s)",
                added,
                region_code,
                wave_theme,
            )
        return added
    except Exception as e:  # noqa: BLE001 — журнал не важнее волны
        logger.warning("skipped duplicates: запись не удалась (%s): %s", region_code, e)
        return 0


async def prune_skipped(session, *, keep_days: int = 7) -> int:
    """Удалить записи старше ``keep_days``. Возвращает число удалённых.

    Запись нужна ровно столько, сколько пост может ещё оказаться кандидатом, то
    есть ``max_post_age_hours`` (дефолт 72 ч). Неделя — это тот же срок с запасом.
    Побочный смысл: ложное срабатывание текстового дедупа само рассасывается через
    неделю, а не запирает пост навсегда.
    """
    from sqlalchemy import delete, select

    from database.models_extended import SkippedDuplicate

    cutoff = datetime.utcnow() - timedelta(days=max(1, int(keep_days)))
    doomed: List[int] = list(
        (
            await session.execute(
                select(SkippedDuplicate.id).where(SkippedDuplicate.detected_at < cutoff)
            )
        )
        .scalars()
        .all()
    )
    if not doomed:
        return 0
    await session.execute(delete(SkippedDuplicate).where(SkippedDuplicate.id.in_(doomed)))
    await session.commit()
    return len(doomed)


def merge_dedup_lips(base: Iterable[str], skipped: Iterable[str]) -> List[str]:
    """Слить курсор публикаций с журналом дублей в один список для парсера.

    Парсер принимает ``work_table_lip`` списком и проверяет вхождение — ему всё
    равно, откуда lip приехал. Слияние отдельной функцией, а не выражением на
    месте, чтобы источник каждого lip'а был виден в тесте: смешать «опубликовано»
    и «отсеяно» в одну кучу можно только на входе парсера и нигде больше.
    """
    return list(dict.fromkeys([*(base or []), *(skipped or [])]))
