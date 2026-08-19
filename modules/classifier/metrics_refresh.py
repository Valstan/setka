"""Обновление метрик собранных постов — данные под рейтинг (звено 5, шаг 1).

Метрики в момент сбора почти нулевые: пост собирается через минуты после
публикации, и лайков у него ещё нет. Поэтому рейтинг строится не на том, что
видел сбор, а на том, что доросло за окно отсева.

**Границы прохода — правила владельца, не оптимизация:**

* **не трогаем посты старше окна** (:mod:`modules.classifier.audit_window`) —
  они всё равно отсеются по старости, и тратить на них вызовы ВК незачем;
* **не трогаем уже опубликованное нами** (``work_tables.lip``) — их рейтинг
  ни на что не влияет, пост из мешка уже ушёл;
* **берём обе стороны аудита, ``kept`` и ``dropped``.** Без метрик на
  отсеянных нельзя проверить находку D-024 (ИИ считает публикуемыми 43% того,
  что выкинули алгоритмы), а именно на неё опирается будущее снятие фильтров.

Объём посчитан на проде 2026-08-19: окно 72 часа = 7774 строки по 29 регионам,
то есть 78 батчей за круг и ~620 вызовов в сутки при прогоне раз в 3 часа.
Батчи идут через общий с парсером per-token тормоз (см.
``vk_monitor/post_metrics.py``), поэтому круг занимает десятки секунд — время,
на которое сессия БД специально отпускается (см. :func:`refresh_metrics`).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from modules.classifier.audit_window import AUDIT_WINDOW_HOURS, in_window, window_cutoff
from modules.vk_monitor.post_metrics import Ref

logger = logging.getLogger(__name__)

_WALL_RE = re.compile(r"wall(-?\d+)_(\d+)\s*$")

# Поля метрик, которые таска пишет в аудит. Порядок не важен, важен состав:
# всё, чего ВК не прислал, не должно затирать уже измеренное.
_METRIC_FIELDS = ("views", "likes", "comments", "reposts")


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
    """Все lip'ы, опубликованные нами, из ``work_tables.lip`` (JSON-списки).

    В эту колонку пишут четыре разных модуля (``cascaded_bulletin``,
    ``copy_setka_network``, ``krugozor_broadcast``, ``telegram_gonba_mirror``),
    и схема JSON ничего не гарантирует. Не-список здесь бросал бы
    ``TypeError``, который внешний ``try/except`` Celery-таски превращает в
    ``ok: False`` на КАЖДОМ круге раз в 3 часа — метрики перестали бы
    обновляться совсем, а заметно это было бы только в логе. Поэтому битую
    строку пропускаем с предупреждением, а круг доезжает на остальных.
    """
    from sqlalchemy import select

    from database.models_extended import WorkTable

    out: Set[str] = set()
    bad = 0
    rows = (await session.execute(select(WorkTable.lip))).all()
    for (lips,) in rows:
        if lips is None:
            continue
        if not isinstance(lips, (list, tuple)):
            bad += 1
            continue
        for lip in lips:
            out.add(str(lip))
    if bad:
        logger.warning(
            "load_published_lips: %d строк work_tables.lip не список — пропущены; "
            "их публикации не будут исключены из обновления метрик",
            bad,
        )
    return out


async def select_refresh_candidates(
    session,
    *,
    hours: int = AUDIT_WINDOW_HOURS,
) -> Tuple[List[Tuple[Ref, str]], int]:
    """Посты аудита в окне ``hours`` → ``(кандидаты, сколько url не разобрано)``.

    Окно — общее с витриной (:mod:`modules.classifier.audit_window`): то, что
    мы меряем, и то, что показываем, обязано совпадать, иначе на панели
    появятся строки, метрики которых никто не обновляет.

    Число неразобранных ``post_url`` возвращается, а не теряется молча:
    приёмочная проверка «доля ``views IS NOT NULL`` около 90%» без него
    показывала бы необъяснимое расхождение.
    """
    from sqlalchemy import select

    from database.models_extended import CollectedPostAudit

    cutoff = window_cutoff(hours)
    stmt = (
        select(CollectedPostAudit.post_url, CollectedPostAudit.lip)
        .where(in_window(cutoff))
        .order_by(CollectedPostAudit.collected_at.desc())
    )

    out: List[Tuple[Ref, str]] = []
    unparsable = 0
    for url, lip in (await session.execute(stmt)).all():
        ref = ref_from_post_url(url)
        if ref is None:
            unparsable += 1
            continue
        out.append((ref, lip))
    if unparsable:
        logger.warning(
            "select_refresh_candidates: %d строк аудита с неразбираемым post_url — "
            "метрики по ним не обновятся",
            unparsable,
        )
    return out, unparsable


def _rowcount(result) -> int:
    """``rowcount`` драйвера, приведённый к неотрицательному числу.

    Некоторые драйверы отдают -1, когда счёт недоступен; отрицательное число в
    сумме «сколько строк изменилось» врало бы в другую сторону.
    """
    return max(int(getattr(result, "rowcount", 0) or 0), 0)


async def apply_metrics(
    session, metrics_by_ref: Dict[Ref, Dict[str, Any]], lip_by_ref: Dict[Ref, str]
) -> int:
    """Записать метрики в аудит. Возвращает число РЕАЛЬНО изменённых строк.

    Считаем ``rowcount``, а не количество ответов ВК: гейт
    ``no_metrics_fetched`` в :func:`refresh_metrics` должен значить «в БД
    ничего не поменялось», иначе он снова разрешит тихий круг вхолостую.

    ``published_at`` перезаписывается только когда его ещё нет: дата поста не
    меняется, а ответ ВК может её и не принести.

    **Отсутствующее не затирает измеренное.** Если ВК не прислал поле (сменился
    токен в карусели, community-token не видит просмотры), прежнее значение
    остаётся, и ``metrics_updated_at`` не штампуется на пустом месте: иначе
    строка становилась бы одновременно «свежей» и «не меренной», а витрина
    показывала бы прочерк на посте, который вчера был измерен.
    """
    from sqlalchemy import update

    from database.models_extended import CollectedPostAudit

    now = datetime.utcnow()
    updated = 0
    for ref, m in metrics_by_ref.items():
        lip = lip_by_ref.get(ref)
        if not lip:
            continue
        measured = {f: m[f] for f in _METRIC_FIELDS if m.get(f) is not None}
        published_at = m.get("published_at")
        if not measured and not published_at:
            continue

        values: Dict[str, Any] = dict(measured)
        if measured:
            # Штамп свежести — только когда есть что штамповать.
            values["metrics_updated_at"] = now

        stmt = update(CollectedPostAudit).where(CollectedPostAudit.lip == lip)
        if published_at:
            res = await session.execute(
                stmt.where(CollectedPostAudit.published_at.is_(None)).values(
                    published_at=published_at, **values
                )
            )
            updated += _rowcount(res)
            if values:
                res = await session.execute(
                    stmt.where(CollectedPostAudit.published_at.isnot(None)).values(**values)
                )
                updated += _rowcount(res)
        else:
            res = await session.execute(stmt.values(**values))
            updated += _rowcount(res)
    await session.commit()
    return updated


async def refresh_metrics(session, *, hours: int = AUDIT_WINDOW_HOURS) -> Dict[str, Any]:
    """Один круг обновления метрик.

    Сама функция исключения не ловит — их ловит обвязка вокруг ``run_coro``
    в Celery-таске ``tasks.celery_app.refresh_post_metrics``. Здесь только
    явные неуспехи (нет токена, ВК не отдал ни одной метрики) — они
    возвращаются как результат, а не бросаются.
    """
    import vk_api

    from modules.vk_monitor.post_metrics import fetch_metrics_for_token
    from modules.vk_token_router import get_healthy_read_token

    candidates, unparsable = await select_refresh_candidates(session, hours=hours)
    published = await load_published_lips(session)
    live = drop_already_published(candidates, published)
    skipped = len(candidates) - len(live)
    if not live:
        return {
            "ok": True,
            "checked": 0,
            "updated": 0,
            "skipped_published": skipped,
            "unparsable_urls": unparsable,
        }

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
            "unparsable_urls": unparsable,
        }

    # Отпускаем соединение с БД перед походом в ВК. Круг из 78 батчей идёт
    # через per-token тормоз (0.4 с на вызов) — это полминуты-минута, восемь
    # раз в сутки. Держать всё это время открытую транзакцию значит держать
    # Postgres в idle-in-transaction: висит соединение из пула, а с ним
    # горизонт autovacuum. Ничего из выбранного не протухнет: дальше в руках
    # только кортежи (ref, lip), ORM-объектов мы не держим.
    await session.commit()

    api = vk_api.VkApi(token=token).get_api()
    lip_by_ref = {ref: lip for ref, lip in live}
    metrics = fetch_metrics_for_token(api, [ref for ref, _ in live], token=token)

    # Транзакция под запись открывается заново — следующим обращением к сессии.
    updated = await apply_metrics(session, metrics, lip_by_ref)
    if updated == 0:
        # Токен был, но в БД ничего не поменялось (бан токена посреди прохода,
        # сетевой сбой на всех батчах разом, ответы без единой метрики) —
        # fetch_metrics_for_token глотает отказы по-батчево и молча вернёт
        # пустой словарь. «Проверено много, обновлено ноль» — это отказ, а не
        # успех: без этой ветки он повторил бы инцидент 2026-08-19, где таска
        # трое суток рапортовала успех, ничего не сделав.
        logger.warning(
            "refresh_metrics: %d постов проверено, ни одна строка не изменилась — "
            "метрики от ВК не пришли ни на один батч",
            len(live),
        )
        return {
            "ok": False,
            "error": "no_metrics_fetched",
            "checked": len(live),
            "updated": 0,
            "skipped_published": skipped,
            "unparsable_urls": unparsable,
        }
    return {
        "ok": True,
        "checked": len(live),
        "updated": updated,
        "skipped_published": skipped,
        "unparsable_urls": unparsable,
    }
