"""Probe: эмпирический охват потока «Кругозор» — 1× vs 2× (обеденный слот 13:00?).

Запрос владельца 2026-06-14 (PENDING, ⏳ «Кругозор»): после ~недели работы
дайджеста посчитать охват → «при хорошем охвате +обед 13:00». Запланировано
«через ~неделю», на 2026-06-24 прошло 10 дней — пора снять точку.

Поток «Кругозор» (modules/krugozor_broadcast.py) публикует научпоп-дайджест нативным
постом (заголовок ``HEADER``) на стены всех целевых регионов 1×/день (beat 20:00 MSK).
Опубликованные посты НИГДЕ не трекаются (нет таблицы) — поэтому ищем их прямо на
стенах: ``wall.get`` по каждому региону, фильтруем по заголовку дайджеста, читаем
``views/likes/reposts`` (user-токен админа видит просмотры).

Чтобы рекомендация была **относительной, а не абсолютной** (measure-before-promote):
для каждого региона тут же замеряем baseline — медиану просмотров обычных локальных
постов (не-дайджест, не-закреп) в том же окне. «Хороший охват» = дайджест читают
сопоставимо с местными новостями. Сравниваем только **дозревшие** посты (старше
``MIN_AGE_HOURS``), т.к. просмотры VK набираются в первые сутки — иначе свежий пост
выглядел бы «провальным» нечестно.

Read-only: только ``wall.get``, ничего не пишем и не меняем.

Запуск на проде: sudo + source /etc/setka/setka.env (root-only env), затем
``./venv/bin/python scripts/probe_krugozor_reach.py``. Вывод — JSON в stdout.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from statistics import median
from typing import Any, Dict, List, Optional

# Окно и пороги замера (можно подвинуть env'ом при желании, но дефолты разумны).
MAX_WINDOW_DAYS = 21  # не смотреть посты старше — «recent» теряет смысл
MIN_AGE_HOURS = 24  # сравнивать только дозревшие посты (просмотры набраны)
WALL_FETCH = 100  # постов на регион (max wall.get) — хватает покрыть окно
BASELINE_SAMPLE = 12  # сколько локальных постов брать на baseline региона
# Префикс заголовка научпоп-постов «Кругозора». Намеренно по префиксу, а не по полному
# заголовку: ловит и старый «🔭 Научпоп-дайджест», и новый «🔭 Научпоп-сводка» (рефактор
# терминологии 2026-06-24) — замер не рвётся на границе переименования.
KRUGOZOR_HEADER_PREFIX = "🔭 Научпоп-"
MEANINGFUL_BASELINE = 20  # регион с медианой локальных просмотров ниже — мёртвая/крошечная
#                           стена: её ratio шумит (5 просмотров = 100%), в типичный
#                           показатель не берём.


def _metrics(post: Dict[str, Any]) -> Dict[str, int]:
    """Просмотры/лайки/репосты поста (count-поля VK, 0 если нет). Чистая."""
    return {
        "views": int((post.get("views") or {}).get("count", 0)),
        "likes": int((post.get("likes") or {}).get("count", 0)),
        "reposts": int((post.get("reposts") or {}).get("count", 0)),
    }


def _is_digest(post: Dict[str, Any], header: str) -> bool:
    """Пост — научпоп-дайджест «Кругозора» (по заголовку). Чистая."""
    return (post.get("text") or "").lstrip().startswith(header)


def _msk_day(ts: int) -> str:
    """Календарный день поста в MSK (UTC+3), 'YYYY-MM-DD'. Чистая."""
    return datetime.fromtimestamp(int(ts) + 3 * 3600, tz=timezone.utc).strftime("%Y-%m-%d")


def _summary(values: List[int]) -> Dict[str, Any]:
    """Свод по списку метрик: n / avg / median / min / max. Чистая."""
    if not values:
        return {"n": 0, "avg": 0, "median": 0, "min": 0, "max": 0}
    return {
        "n": len(values),
        "avg": round(sum(values) / len(values), 1),
        "median": round(float(median(values)), 1),
        "min": min(values),
        "max": max(values),
    }


def _ratio(a: float, b: float) -> Optional[float]:
    """a/b в %, None если базы нет. Чистая."""
    if not b:
        return None
    return round(100.0 * a / b, 1)


def _recommend(rows: List[Dict[str, Any]], pooled_digest: List[int]) -> Dict[str, Any]:
    """Рекомендация 1× vs 2× по охвату дайджеста относительно локальных постов.

    **Метрика — медиана per-region отношений, НЕ pooled.** Pooled-сравнение
    (медиана всех дайджестов / медиана всех локальных) даёт парадокс Симпсона: пул
    смешивает крупные и мёртвые стены в разных пропорциях и завышает показатель.
    Честный «типичный регион» — медиана отношений по регионам с осмысленным baseline
    (≥ ``MEANINGFUL_BASELINE`` просмотров; крошечные стены, где 5 просмотров = «100%»,
    отбрасываем как шум).

    Эвристика порогов (озвучить владельцу, не догма): дайджест уверенно «читают», если
    типичный регион даёт ему ≥ 50% просмотров локального поста → 2-й слот (обед 13:00)
    оправдан. < 25% — слабо заходит, 2× лишь разбавит ленту. Между — оставить 1×,
    перезамерить через ~2 недели. Чистая."""
    ratios = [
        r["digest_vs_local_pct"]
        for r in rows
        if r["digest_vs_local_pct"] is not None
        and r["baseline_views_local"]["median"] >= MEANINGFUL_BASELINE
    ]
    typ = round(float(median(ratios)), 1) if ratios else None
    pooled_pct = _ratio(
        float(median(pooled_digest)) if pooled_digest else 0.0,
        float(median([v for r in rows for v in [r["baseline_views_local"]["median"]]])) or 0.0,
    )
    if not pooled_digest:
        verdict, why = "INSUFFICIENT", "дайджестов в окне не найдено — рано судить"
    elif typ is None:
        verdict, why = "INSUFFICIENT", "нет регионов с осмысленным baseline для сравнения"
    elif typ >= 50:
        verdict = "ADD_LUNCH_SLOT"
        why = (
            f"типичный регион даёт дайджесту {typ}% просмотров локального поста "
            "(≥50%) — 2× оправдан"
        )
    elif typ < 25:
        verdict = "KEEP_1X"
        why = f"типичный регион даёт лишь {typ}% (<25%) — 2× разбавит ленту"
    else:
        verdict = "KEEP_1X_REMEASURE"
        why = (
            f"охват средний ({typ}% от локального в типичном регионе) — "
            "оставить 1×, при желании владельца включить 2× как измеряемый эксперимент"
        )
    return {
        "verdict": verdict,
        "why": why,
        "typical_region_digest_vs_local_pct": typ,
        "regions_considered": len(ratios),
        "pooled_digest_vs_local_pct": pooled_pct,
        "pooled_note": "pooled завышает (парадокс Симпсона) — ориентир по typical_region_*",
    }


async def _target_regions(session) -> List[Dict[str, Any]]:
    """Целевые регионы «Кругозора» — та же выборка, что в самом модуле."""
    from sqlalchemy import select

    from config.runtime import get_krugozor_target_region_codes
    from database.models import Region

    region_filter = get_krugozor_target_region_codes()
    q = select(Region).where(
        Region.is_active.is_(True),
        Region.vk_group_id.isnot(None),
        Region.code != "copy",
    )
    if region_filter:
        q = q.where(Region.code.in_(list(region_filter)))
    regions = list((await session.execute(q)).scalars().all())
    return [{"code": r.code, "name": r.name or r.code, "gid": int(r.vk_group_id)} for r in regions]


async def main() -> None:
    from modules.vk_monitor.vk_client import VKClient
    from modules.vk_token_router import load_vk_routing

    user_token, _community_tokens = await load_vk_routing()
    if not user_token:
        print(json.dumps({"error": "no user token (COMMUNITY_WRITE)"}, ensure_ascii=False))
        return

    from database.connection import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        regions = await _target_regions(session)
    if not regions:
        print(json.dumps({"error": "no target regions"}, ensure_ascii=False))
        return

    now_ts = int(time.time())
    window_floor = now_ts - MAX_WINDOW_DAYS * 86400
    mature_ceiling = now_ts - MIN_AGE_HOURS * 3600

    client = VKClient(user_token)
    all_digest_views: List[int] = []
    all_digest_likes: List[int] = []
    all_digest_reposts: List[int] = []
    all_baseline_views: List[int] = []
    per_day: Dict[str, List[int]] = {}
    rows: List[Dict[str, Any]] = []

    for reg in regions:
        posts = await asyncio.to_thread(client.get_wall_posts, -abs(reg["gid"]), WALL_FETCH, 0)
        d_views: List[int] = []
        d_likes: List[int] = []
        d_reposts: List[int] = []
        b_views: List[int] = []
        digests: List[Dict[str, Any]] = []
        for p in posts or []:
            ts = int(p.get("date") or 0)
            if ts < window_floor or ts > mature_ceiling:
                continue  # вне окна / ещё не дозрел
            m = _metrics(p)
            if _is_digest(p, KRUGOZOR_HEADER_PREFIX):
                d_views.append(m["views"])
                d_likes.append(m["likes"])
                d_reposts.append(m["reposts"])
                per_day.setdefault(_msk_day(ts), []).append(m["views"])
                digests.append({"day": _msk_day(ts), **m})
            elif not p.get("is_pinned") and len(b_views) < BASELINE_SAMPLE:
                b_views.append(m["views"])

        all_digest_views += d_views
        all_digest_likes += d_likes
        all_digest_reposts += d_reposts
        all_baseline_views += b_views
        dm = float(median(d_views)) if d_views else 0.0
        bm = float(median(b_views)) if b_views else 0.0
        rows.append(
            {
                "code": reg["code"],
                "name": reg["name"],
                "digests_found": len(d_views),
                "digest_views": _summary(d_views),
                "digest_likes": _summary(d_likes),
                "baseline_views_local": _summary(b_views),
                "digest_vs_local_pct": _ratio(dm, bm),
                "digest_posts": digests,
            }
        )

    by_day = {
        day: _summary(v) for day, v in sorted(per_day.items(), key=lambda kv: kv[0], reverse=True)
    }

    out = {
        "probe": "krugozor_reach",
        "window_days": MAX_WINDOW_DAYS,
        "min_age_hours": MIN_AGE_HOURS,
        "regions_scanned": len(regions),
        "regions_with_digest": sum(1 for r in rows if r["digests_found"] > 0),
        "publish_days_found": len(per_day),
        "total_digest_posts": len(all_digest_views),
        "overall": {
            "digest_views": _summary(all_digest_views),
            "digest_likes": _summary(all_digest_likes),
            "digest_reposts": _summary(all_digest_reposts),
            "baseline_local_views": _summary(all_baseline_views),
        },
        "by_publish_day": by_day,
        "recommendation": _recommend(rows, all_digest_views),
        "per_region": sorted(rows, key=lambda r: -(r["digest_views"]["median"])),
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
