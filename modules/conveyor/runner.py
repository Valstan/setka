"""Прогон конвейера по одному сайту: отбор → классификация → доставка (D-015).

Три звена в **одном прогоне**, а не в трёх отложенных очередях — и это не
удобство, а требование: ссылки на медиа в аудите живут ровно столько, сколько
живут у ВК-CDN (G56/G63). Отложив доставку на «когда дойдут руки», конвейер
исправно отправлял бы новости с битыми картинками, и заметили бы это читатели.

Каждый пост проходит свой путь независимо: отказ на одном не мешает остальным.
Всё, что случилось, попадает в ``conveyor_deliveries`` — включая отказы, потому
что «почему этой новости нет на сайте» должно иметь ответ строкой в журнале.

Shadow-фаза (§D плана): приёмник создаёт **только черновики**, публикует человек.
Автопубликация появится не здесь, а на стороне сайта — и только когда agree-rate
этого сайта наберётся на реальном потоке.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional

from config.content_conveyor import get_batch_max, get_ingest_key, get_source_days
from modules.conveyor import classify as classify_mod
from modules.conveyor import delivery as delivery_mod
from modules.conveyor import source as source_mod

logger = logging.getLogger(__name__)

# Пауза между постами: DeepSeek ограничивает частоту, а конвейер не в горячем
# пути — торопиться некуда. Секунды, не миллисекунды: 20 постов × 1 с — это
# 20 секунд на прогон, что для трёх запусков в сутки несущественно.
DEFAULT_PACE_SECONDS = 1.0


async def run_site(
    session,
    site: Dict[str, Any],
    *,
    rules: str = "",
    days: Optional[int] = None,
    limit: Optional[int] = None,
    dry_run: bool = False,
    pace: float = DEFAULT_PACE_SECONDS,
    sleep: Optional[Callable[[float], None]] = None,
) -> Dict[str, Any]:
    """Один прогон по сайту. Возвращает сводку: сколько отобрано и чем кончилось.

    ``dry_run`` — пройти весь путь **без** классификации и доставки: отбор
    выполняется, журнал не пишется, наружу ничего не уходит. Нужен, чтобы
    посмотреть на живых данных, что именно конвейер собирается отправить, до
    того как он это отправит.
    """
    site_key = str(site.get("key") or "").strip().lower()
    stats: Dict[str, Any] = {
        "site": site_key,
        "selected": 0,
        "delivered": 0,
        "rejected": 0,
        "held": 0,
        "failed": 0,
        "unknown_sections": [],
        "tokens": 0,
        "dry_run": bool(dry_run),
    }

    posts = await source_mod.fetch_pending_for_site(
        session,
        site,
        days=days if days is not None else get_source_days(),
        limit=limit if limit is not None else get_batch_max(),
    )
    stats["selected"] = len(posts)
    if not posts:
        return stats
    if dry_run:
        stats["preview"] = [
            {
                "lip": p["lip"],
                "theme": p["theme"],
                "chars": len(p["text"]),
                "media": len(p["media"]),
            }
            for p in posts
        ]
        return stats

    key = get_ingest_key(site)
    sections = site.get("sections") or ()
    await source_mod.record_selection(session, site=site_key, lips=[p["lip"] for p in posts])
    await session.commit()

    results: List[Dict[str, Any]] = []
    for i, post in enumerate(posts):
        if i and pace and sleep is not None:
            sleep(pace)
        outcome = await _process_one(session, site, post, key=key, sections=sections, rules=rules)
        results.append(outcome)
        stats[outcome["bucket"]] = stats.get(outcome["bucket"], 0) + 1
        stats["tokens"] += outcome.get("tokens") or 0
        await session.commit()

    stats["unknown_sections"] = classify_mod.collect_unknown_sections(
        [r["classify"] for r in results if r.get("classify")]
    )
    if stats["unknown_sections"]:
        # Не глушим и не подгоняем под черновой список рубрик приёмника —
        # директива просит вернуть, какая нарезка просится по факту потока.
        logger.info(
            "конвейер %s: рубрики вне списка сайта — %s",
            site_key,
            ", ".join(stats["unknown_sections"]),
        )
    return stats


async def _process_one(
    session,
    site: Dict[str, Any],
    post: Dict[str, Any],
    *,
    key: str,
    sections,
    rules: str,
) -> Dict[str, Any]:
    """Путь одного поста. Никогда не бросает — падение на одном не рушит прогон."""
    site_key = str(site.get("key") or "").strip().lower()
    lip = str(post.get("lip") or "")
    try:
        # api_key НЕ передаём: у classify свой ключ (DeepSeek), а `key` здесь —
        # ключ доставки на сайт. Это разные секреты разных сторон, и смешать их
        # означало бы отправить ключ сайта в чужой API.
        verdict_res = classify_mod.classify(post, sections=sections, rules=rules)
    except Exception as e:  # pragma: no cover — защитный контур
        logger.warning("конвейер %s: классификация упала на %s: %s", site_key, lip, e)
        verdict_res = {"ok": False, "reason": "classify_crashed"}

    tokens = ((verdict_res.get("usage") or {}) or {}).get("total_tokens") or 0
    if not verdict_res.get("ok"):
        reason = str(verdict_res.get("reason") or "llm_failed")
        # Отказ модели — это решение («не для сайта»), а сбой связи — нет.
        # Первое закрывает пост навсегда, второе оставляет строку в failed,
        # чтобы следующий прогон её не подобрал молча как новую.
        bucket = "rejected" if reason.startswith("llm_reject") else "failed"
        await source_mod.update_delivery(
            session, site=site_key, lip=lip, status=bucket, reason=reason
        )
        return {"bucket": bucket, "tokens": tokens, "classify": verdict_res}

    verdict = verdict_res["verdict"]
    body = delivery_mod.build_payload(post, verdict)
    bad = delivery_mod.check_invariant(body)
    if bad:
        # Задержан, а не выброшен: строка в журнале со статусом held — это то,
        # что человек сможет посмотреть и решить сам (pool #133).
        await source_mod.update_delivery(
            session, site=site_key, lip=lip, status="held", reason=bad, verdict=verdict
        )
        return {"bucket": "held", "tokens": tokens, "classify": verdict_res}

    res = delivery_mod.deliver(site, key, body, sleep=time.sleep)
    if res.get("ok"):
        await source_mod.update_delivery(
            session,
            site=site_key,
            lip=lip,
            status="delivered",
            verdict=verdict,
            attempts=res.get("attempts"),
            http_status=res.get("status"),
            remote_id=res.get("remote_id"),
        )
        return {"bucket": "delivered", "tokens": tokens, "classify": verdict_res}

    await source_mod.update_delivery(
        session,
        site=site_key,
        lip=lip,
        status="failed",
        reason=str(res.get("reason") or "delivery_failed"),
        verdict=verdict,
        attempts=res.get("attempts"),
        http_status=res.get("status"),
    )
    return {"bucket": "failed", "tokens": tokens, "classify": verdict_res}
