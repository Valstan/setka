"""Одиночный пост против сводки: даёт ли форма охват — замер по сети (read-only).

**Зачем.** 26.08 в Кирсе одиночный пост собрал 717 853 просмотра при 5 подписчиках,
а сводка о том же событии, вышедшая в ту же минуту, — 332. Разница в две тысячи раз
породила решение «публиковать одиночными, а не сборниками». Но это **одно наблюдение**,
и в нём форма и содержание изменились вместе: одиночный нёс смерть земляка с именем,
сводка — анонс церемонии прощания. Одним наблюдением их не разделить.

**Как разделяем.** Не «до и после», а **внутри одного региона и одного дня**. Обе формы
у нас сосуществовали: сводка получается, когда в слот набралось несколько отобранных
постов, одиночный — когда набрался один. Сравнение внутри пары «регион + день» снимает
разом подписчиков, сезон, район и день недели — то, чем сравнение периодов отравлено
по построению.

**Метрика — медиана, не среднее.** Один вирусный пост уносит среднее куда угодно;
именно так и появился исходный вопрос.

**Чего этот замер НЕ может и не будет делать вид, что может.** Форма у нас не
назначалась случайно. Если срочные новости системно выходили в одиночку, а рутина
пачкой, разница окажется про **содержание**, а не про форму, и никакая статистика
этого не разведёт без настоящей рандомизации. Скрипт печатает величину эффекта и эту
оговорку рядом — вывод делает человек.

**Форма поста** считается по маркеру ``✍``: ``modules/publisher/bulletin_builder.py``
ставит его перед каждым элементом сводки (``POST_MARKER``), число элементов ограничено
``max_posts_per_bulletin`` (1..10, дефолт 3). Один маркер — одиночный, больше — сводка.

Запуск на проде (read-only, ничего не пишет):

    python3 scripts/probe_post_form_reach.py                 # вся активная сеть
    python3 scripts/probe_post_form_reach.py --pages 6       # глубже назад по стене
    python3 scripts/probe_post_form_reach.py mi verhnekame   # выборочно
    python3 scripts/probe_post_form_reach.py --json > /tmp/form.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

POST_MARKER = "✍"


# --- чистая часть: её проверяют тесты ------------------------------------------------


def item_count(text: str) -> int:
    """Сколько элементов в теле поста. 0 — маркера нет (не наша вёрстка)."""
    return (text or "").count(POST_MARKER)


def post_form(text: str) -> Optional[str]:
    """``single`` / ``digest`` / None, если пост не размечен нашим маркером."""
    n = item_count(text)
    if n <= 0:
        return None
    return "single" if n == 1 else "digest"


def paired_days(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Пары «регион + день», где в один день вышли ОБЕ формы.

    Только такие пары и сравнимы: в них подписчики, сезон, район и день недели
    одинаковы у обеих групп по построению, а не по допущению.
    """
    buckets: Dict[Tuple[str, str], Dict[str, List[int]]] = defaultdict(
        lambda: {"single": [], "digest": []}
    )
    for r in rows:
        form = r.get("form")
        if form not in ("single", "digest"):
            continue
        buckets[(r["region"], r["day"])][form].append(int(r.get("views") or 0))

    out: List[Dict[str, Any]] = []
    for (region, day), forms in sorted(buckets.items()):
        if not forms["single"] or not forms["digest"]:
            continue
        out.append(
            {
                "region": region,
                "day": day,
                "n_single": len(forms["single"]),
                "n_digest": len(forms["digest"]),
                "med_single": statistics.median(forms["single"]),
                "med_digest": statistics.median(forms["digest"]),
            }
        )
    return out


def summarize(pairs: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Сводка по парам. ``lift`` — отношение медиан внутри дня, усреднённое медианой.

    Нулевую медиану сводки в отношение не берём (делить нельзя) — такие дни
    считаются отдельно и печатаются, а не растворяются в результате.
    """
    lifts: List[float] = []
    single_wins = digest_wins = ties = undefined = 0
    for p in pairs:
        ms, md = p["med_single"], p["med_digest"]
        if md > 0:
            lifts.append(ms / md)
        else:
            undefined += 1
        if ms > md:
            single_wins += 1
        elif md > ms:
            digest_wins += 1
        else:
            ties += 1
    return {
        "dney_s_oboimi_formami": len(pairs),
        "odinochnyy_vyigral": single_wins,
        "svodka_vyigrala": digest_wins,
        "nichya": ties,
        "mediana_otnosheniya": statistics.median(lifts) if lifts else None,
        "dney_bez_otnosheniya": undefined,
    }


# --- сбор данных ---------------------------------------------------------------------


async def fetch_wall(client: Any, owner_id: int, pages: int) -> Tuple[List[dict], Optional[str]]:
    """Стена группы постранично. Возвращает (посты, причина остановки)."""
    items: List[dict] = []
    for page in range(pages):
        resp = await asyncio.to_thread(
            client.api_call,
            "wall.get",
            {"owner_id": owner_id, "count": 100, "offset": page * 100, "extended": 0},
        )
        if not isinstance(resp, dict):
            return items, f"неожиданный ответ {type(resp).__name__}"
        if resp.get("error"):
            return items, f"VK error {resp['error']}"
        got = resp.get("items")
        if got is None:
            return items, "в ответе нет items"
        if not got:
            return items, None
        items.extend(got)
    return items, None


async def collect(codes: Optional[Iterable[str]], pages: int) -> Dict[str, Any]:
    from sqlalchemy import select

    from database.connection import AsyncSessionLocal
    from database.models import Region
    from modules.vk_monitor.vk_client import VKClient
    from tasks.discovery_tasks import _pick_parse_token

    token = await _pick_parse_token()
    if not token:
        raise RuntimeError("нет VK parse-токена (VK_TOKENS пуст или все в cooldown)")
    client = VKClient(token=token)

    async with AsyncSessionLocal() as session:
        stmt = select(Region).where(Region.vk_group_id.isnot(None)).order_by(Region.code)
        if codes:
            stmt = stmt.where(Region.code.in_(list(codes)))
        regions = list((await session.execute(stmt)).scalars())

    rows: List[Dict[str, Any]] = []
    skipped: List[str] = []
    for reg in regions:
        posts, why = await fetch_wall(client, int(reg.vk_group_id), pages)
        if why:
            # «не смог посмотреть» ≠ «постов нет»: регион уходит в отдельный список,
            # а не растворяется нулём в статистике.
            skipped.append(f"{reg.code}: {why}")
        for it in posts:
            form = post_form(it.get("text") or "")
            if form is None:
                continue
            ts = int(it.get("date") or 0)
            if not ts:
                continue
            d = datetime.fromtimestamp(ts, tz=timezone.utc)
            rows.append(
                {
                    "region": reg.code,
                    "day": f"{d:%Y-%m-%d}",
                    "form": form,
                    "items": item_count(it.get("text") or ""),
                    "views": (it.get("views") or {}).get("count", 0),
                    "likes": (it.get("likes") or {}).get("count", 0),
                    "reposts": (it.get("reposts") or {}).get("count", 0),
                    "post_id": it.get("id"),
                }
            )
    return {"rows": rows, "skipped": skipped, "regions": len(regions)}


def render(data: Dict[str, Any]) -> str:
    rows = data["rows"]
    pairs = paired_days(rows)
    s = summarize(pairs)

    out: List[str] = []
    out.append(f"регионов опрошено: {data['regions']}, постов с нашей вёрсткой: {len(rows)}")
    if data["skipped"]:
        out.append(f"НЕ СМОГЛИ прочитать целиком ({len(data['skipped'])}):")
        for line in data["skipped"][:10]:
            out.append(f"  - {line}")

    n_single = sum(1 for r in rows if r["form"] == "single")
    n_digest = len(rows) - n_single
    out.append(f"одиночных {n_single} · сводок {n_digest}")

    out.append("")
    out.append("=== сравнение ВНУТРИ дня и региона (единственное честное) ===")
    out.append(f"дней, где вышли обе формы: {s['dney_s_oboimi_formami']}")
    if not s["dney_s_oboimi_formami"]:
        out.append("Сравнивать нечего: обе формы ни разу не встретились в одном дне.")
        return "\n".join(out)
    out.append(
        f"одиночный выиграл в {s['odinochnyy_vyigral']}, сводка в {s['svodka_vyigrala']}, "
        f"ничья {s['nichya']}"
    )
    if s["mediana_otnosheniya"] is not None:
        out.append(
            f"медиана отношения медиан (одиночный / сводка): {s['mediana_otnosheniya']:.2f}×"
        )
    if s["dney_bez_otnosheniya"]:
        out.append(
            f"дней, где медиана сводки = 0 и отношение не определено: {s['dney_bez_otnosheniya']}"
        )

    out.append("")
    out.append("день · регион      одиноч.  медиана   сводок  медиана")
    for p in pairs[-25:]:
        out.append(
            f"{p['day']} {p['region']:<12} {p['n_single']:5d} {p['med_single']:8.0f} "
            f"{p['n_digest']:8d} {p['med_digest']:8.0f}"
        )

    out.append("")
    out.append("⚠️ Форма не назначалась случайно. Если срочное системно выходило в одиночку,")
    out.append("   а рутина пачкой, эта разница — про содержание, а не про форму. Замер")
    out.append("   показывает величину, а не причину.")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description="одиночный пост против сводки: замер охвата")
    ap.add_argument("codes", nargs="*", help="коды регионов; пусто = вся сеть")
    ap.add_argument("--pages", type=int, default=4, help="страниц стены на регион (по 100 постов)")
    ap.add_argument("--json", action="store_true", help="машинный вывод")
    args = ap.parse_args()

    data = asyncio.run(collect(args.codes or None, max(1, args.pages)))
    if args.json:
        print(json.dumps({**data, "pairs": paired_days(data["rows"])}, ensure_ascii=False))
    else:
        print(render(data))
    return 0


if __name__ == "__main__":
    sys.exit(main())
