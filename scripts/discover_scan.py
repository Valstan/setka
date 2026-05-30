#!/usr/bin/env python3
"""Read-only VK community scanner for human/neural-in-the-loop discovery.

Назначение
==========
Подбор сообществ для пула региона (район ИЛИ область) с **ручной
нейро-классификацией**: скрипт только СОБИРАЕТ сырьё (группы + свежие посты),
а решение «какая тема / брать ли» принимает человек-оператор или Claude в чате,
прочитав посты. Это сознательно НЕ использует Groq/keyword-алгоритмы —
классификацию по сути постов делает нейросеть.

Что делает
==========
1. По списку поисковых запросов (``--queries`` JSON) гоняет ``groups.search``.
2. Дедуп по ``id``; ``discovered_via`` фиксирует, по каким запросам нашлась группа.
3. Обогащает ``groups.getById`` (members_count, description, screen_name, …).
4. Сортирует по ``members_count`` и берёт top-N (``--top``).
5. Для top-N тянет ``--posts`` свежих постов со стены (``wall.get``) — текст
   (для репостов берёт текст оригинала из ``copy_history``).
6. Пишет компактный JSON в ``--out`` (только НЕсекретные данные).

Секреты
=======
Токен читается ТОЛЬКО из переменной окружения ``SCAN_VK_TOKEN`` и **никогда**
не печатается (ни в stdout, ни в stderr, ни в выходной JSON). Запускать на
проде так, чтобы значение токена не попало в лог вызова::

    TOKEN="$(sudo -u postgres psql -d setka -tA -c "SELECT token FROM vk_tokens \
      WHERE community_id IS NULL AND is_active AND token<>'' \
      AND (disabled_until IS NULL OR disabled_until < now()) \
      AND COALESCE(validation_status,'')<>'invalid' \
      ORDER BY last_used NULLS FIRST, id LIMIT 1")"
    SCAN_VK_TOKEN="$TOKEN" /home/valstan/SETKA/venv/bin/python scripts/discover_scan.py \
      --queries /tmp/queries.json --out /tmp/scan_result.json

Зависимости — только ``requests`` (есть в проектном venv). Никаких импортов
проекта/БД — скрипт самодостаточен и переносим.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

import requests

VK_API = "https://api.vk.com/method/"
VK_VERSION = "5.199"
# VK rate-limit для user-токена ~3 запроса/сек. 0.34s между вызовами — с запасом.
SLEEP_BETWEEN_CALLS = 0.34


def _log(msg: str) -> None:
    """Прогресс в stderr (никогда не печатаем токен)."""
    print(msg, file=sys.stderr, flush=True)


def _vk_call(method: str, token: str, params: Dict[str, Any], retries: int = 2) -> Dict[str, Any]:
    """Один вызов VK API. Возвращает содержимое ``response`` или {} при ошибке.

    Обрабатывает error 6 (too many requests) с короткой паузой и retry.
    Токен подмешивается локально — в логи/исключения не попадает.
    """
    call_params = dict(params)
    call_params["access_token"] = token
    call_params["v"] = VK_VERSION
    for attempt in range(retries + 1):
        time.sleep(SLEEP_BETWEEN_CALLS)
        try:
            r = requests.get(VK_API + method, params=call_params, timeout=30)
            data = r.json()
        except Exception as e:  # network / json
            _log(f"  ! {method}: request failed: {e}")
            return {}
        if "error" in data:
            code = data["error"].get("error_code")
            emsg = data["error"].get("error_msg", "")
            if code == 6 and attempt < retries:  # too many requests
                time.sleep(1.0)
                continue
            _log(f"  ! {method}: VK error {code}: {emsg}")
            return {}
        return data.get("response", {})
    return {}


def search_groups(
    token: str, query: str, count: int, city_id: Optional[int]
) -> List[Dict[str, Any]]:
    params: Dict[str, Any] = {"q": query, "count": count, "sort": 0}
    if city_id:
        params["city_id"] = int(city_id)
    resp = _vk_call("groups.search", token, params)
    if isinstance(resp, dict):
        return resp.get("items", []) or []
    return []


def get_by_ids(token: str, ids: List[int], fields: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for i in range(0, len(ids), 500):
        chunk = ids[i : i + 500]
        resp = _vk_call(
            "groups.getById",
            token,
            {"group_ids": ",".join(str(x) for x in chunk), "fields": fields},
        )
        # v5.199 → {"groups": [...]}; старее → [...]
        if isinstance(resp, dict) and "groups" in resp:
            out.extend(resp["groups"] or [])
        elif isinstance(resp, list):
            out.extend(resp)
    return out


def wall_texts(token: str, vk_id: int, posts: int, trunc: int) -> List[str]:
    resp = _vk_call(
        "wall.get",
        token,
        {"owner_id": -abs(int(vk_id)), "count": posts, "filter": "owner"},
    )
    items = resp.get("items", []) if isinstance(resp, dict) else []
    texts: List[str] = []
    for it in items or []:
        txt = (it.get("text") or "").strip()
        if not txt:
            ch = it.get("copy_history") or []
            if ch:
                txt = (ch[0].get("text") or "").strip()
        if not txt:
            # пост без текста — отметим тип вложения, чтобы оператор видел
            atts = it.get("attachments") or []
            kinds = sorted({a.get("type", "?") for a in atts})
            txt = f"[без текста; вложения: {', '.join(kinds)}]" if kinds else "[пустой пост]"
        if len(txt) > trunc:
            txt = txt[:trunc].rstrip() + "…"
        texts.append(txt)
    return texts


def main() -> int:
    ap = argparse.ArgumentParser(description="Read-only VK community scanner")
    ap.add_argument(
        "--queries", required=True, help='JSON file: [{"q":..,"label":..}] или ["q1",..]'
    )
    ap.add_argument("--out", required=True, help="output JSON path")
    ap.add_argument("--count", type=int, default=40, help="results per groups.search")
    ap.add_argument("--top", type=int, default=55, help="top-N by members to fetch posts for")
    ap.add_argument(
        "--per-label-top",
        type=int,
        default=0,
        help="если >0 — берём top-N по members ВНУТРИ каждого label (via), "
        "затем объединяем. Защита нишевых тем от вытеснения общегородскими гигантами",
    )
    ap.add_argument("--posts", type=int, default=5, help="recent posts per candidate")
    ap.add_argument("--post-trunc", type=int, default=220, help="truncate each post text")
    ap.add_argument("--desc-trunc", type=int, default=320, help="truncate description")
    ap.add_argument("--city-id", type=int, default=None, help="optional VK city_id for geo-search")
    ap.add_argument("--min-members", type=int, default=500, help="drop tiny groups before ranking")
    ap.add_argument(
        "--region-filter",
        default=None,
        help="regex (case-insensitive): оставить только группы, чьё name+description "
        "матчит паттерн. Отсекает чужие регионы при fuzzy-выдаче VK "
        "(напр. '(киров|вятк|хлынов)')",
    )
    ap.add_argument(
        "--name-filter",
        default=None,
        help="regex (case-insensitive) по ИМЕНИ группы: оставить только группы, "
        "чьё name матчит тему. Выцепляет профильные/официальные паблики, "
        "отсекая общегородских гигантов (напр. '(сельск|агро|апк|фермер)')",
    )
    args = ap.parse_args()

    token = os.environ.get("SCAN_VK_TOKEN", "").strip()
    if not token:
        _log("FATAL: SCAN_VK_TOKEN env var is empty")
        return 2

    with open(args.queries, encoding="utf-8") as f:
        raw_queries = json.load(f)
    queries: List[Dict[str, str]] = []
    for q in raw_queries:
        if isinstance(q, str):
            queries.append({"q": q, "label": q})
        elif isinstance(q, dict) and q.get("q"):
            queries.append({"q": q["q"], "label": q.get("label", q["q"])})

    # 1. search + dedup
    seen: Dict[int, Dict[str, Any]] = {}
    for spec in queries:
        q, label = spec["q"], spec["label"]
        items = search_groups(token, q, args.count, args.city_id)
        _log(f"search {q!r} ({label}): {len(items)} hits")
        for it in items:
            gid = int(it.get("id") or 0)
            if not gid:
                continue
            if it.get("is_closed"):
                continue
            g = seen.get(gid)
            if g is None:
                seen[gid] = {
                    "vk_id": gid,
                    "name": (it.get("name") or "").strip(),
                    "screen_name": it.get("screen_name"),
                    "type": it.get("type"),
                    "members_count": None,
                    "description": None,
                    "via": [label],
                }
            elif label not in g["via"]:
                g["via"].append(label)

    if not seen:
        _log("no groups found")
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump([], f, ensure_ascii=False)
        return 0

    # 2. enrich
    _log(f"enriching {len(seen)} unique groups…")
    enriched = get_by_ids(
        token,
        list(seen.keys()),
        fields="members_count,description,activity,status,screen_name,photo_200",
    )
    for it in enriched:
        gid = int(it.get("id") or 0)
        g = seen.get(gid)
        if not g:
            continue
        if it.get("members_count") is not None:
            g["members_count"] = it["members_count"]
        if it.get("description"):
            d = (it["description"] or "").strip()
            g["description"] = (
                (d[: args.desc_trunc].rstrip() + "…") if len(d) > args.desc_trunc else d
            )
        if it.get("activity"):
            g["activity"] = it["activity"]
        if it.get("screen_name"):
            g["screen_name"] = it["screen_name"]

    # 2b. region-filter (опц.): отсекаем чужие регионы по name+description.
    # VK groups.search для запросов вида «Министерство X области» fuzzy-матчит
    # аналоги других регионов — этот фильтр их режет автоматически.
    if args.region_filter:
        import re

        rx = re.compile(args.region_filter, re.IGNORECASE)
        before_rf = len(seen)
        for gid in list(seen.keys()):
            g = seen[gid]
            hay = " ".join(filter(None, [g.get("name"), g.get("description")]))
            if not rx.search(hay):
                del seen[gid]
        _log(f"region-filter {args.region_filter!r}: kept {len(seen)}/{before_rf}")
        if not seen:
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump({"candidates": []}, f, ensure_ascii=False)
            return 0

    # 2c. name-filter (опц.): оставляем только группы, чьё ИМЯ про тему.
    # Выцепляет профильные/официальные паблики, отсекая общегородских гигантов.
    if args.name_filter:
        import re

        nrx = re.compile(args.name_filter, re.IGNORECASE)
        before_nf = len(seen)
        for gid in list(seen.keys()):
            if not nrx.search(seen[gid].get("name") or ""):
                del seen[gid]
        _log(f"name-filter {args.name_filter!r}: kept {len(seen)}/{before_nf}")
        if not seen:
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump({"candidates": []}, f, ensure_ascii=False)
            return 0

    # 3. rank + select. Drop tiny, затем либо глобальный top по members, либо
    # (если --per-label-top) top-N внутри каждой темы (label) с объединением —
    # так нишевые темы (спорт/наука/АПК) не вытесняются общегородскими гигантами.
    eligible = [g for g in seen.values() if (g.get("members_count") or 0) >= args.min_members]
    if args.per_label_top > 0:
        by_label: Dict[str, List[Dict[str, Any]]] = {}
        for g in eligible:
            for lbl in g.get("via", []) or ["?"]:
                by_label.setdefault(lbl, []).append(g)
        selected_ids: set[int] = set()
        selected: List[Dict[str, Any]] = []
        for lbl in sorted(by_label):
            gs = sorted(by_label[lbl], key=lambda g: g.get("members_count") or 0, reverse=True)
            for g in gs[: args.per_label_top]:
                if g["vk_id"] not in selected_ids:
                    selected_ids.add(g["vk_id"])
                    selected.append(g)
        selected.sort(key=lambda g: g.get("members_count") or 0, reverse=True)
        top = selected[: args.top]
        _log(
            f"{len(eligible)} groups >= {args.min_members} members; "
            f"per-label top {args.per_label_top} → {len(selected)} selected; "
            f"fetching posts for top {len(top)}"
        )
    else:
        eligible.sort(key=lambda g: g.get("members_count") or 0, reverse=True)
        top = eligible[: args.top]
        _log(
            f"{len(eligible)} groups >= {args.min_members} members; "
            f"fetching posts for top {len(top)}"
        )
    groups = eligible

    # 4. recent posts for top-N
    for i, g in enumerate(top, 1):
        g["recent_posts"] = wall_texts(token, g["vk_id"], args.posts, args.post_trunc)
        if i % 10 == 0:
            _log(f"  posts {i}/{len(top)}")

    out = {
        "scanned_queries": [s["q"] for s in queries],
        "total_unique": len(seen),
        "ranked": len(groups),
        "candidates": top,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    _log(f"wrote {len(top)} candidates → {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
