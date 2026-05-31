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
import re
import sys
import time
from collections import Counter
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


# --------------------------------------------------------------------------- #
# Источники для РАЙОНОВ (помимо groups.search по ручным запросам).
# Всё на сырых VK-вызовах через _vk_call — скрипт остаётся самодостаточным
# (только requests + stdlib, ноль импортов проекта/БД). Логика locality-стема и
# репост-харвеста портирована (не импортирована) из modules/discovery/vk_search.py.
# --------------------------------------------------------------------------- #

_RU_VOWELS = "аеёиоуыэюя"


def make_stem(word: str) -> str:
    """Наивный русский стем: вниз-регистр, срезаем хвостовые гласные.

    Портирован из ``modules/discovery/vk_search.py``. Минимальная длина 3,
    иначе вернётся пустая строка (защита от слишком коротких корней).
    """
    w = "".join(ch for ch in word.lower().strip() if ch.isalnum())
    while len(w) > 3 and w[-1] in _RU_VOWELS:
        w = w[:-1]
    return w if len(w) >= 3 else ""


def locality_stems(localities: List[str]) -> List[str]:
    """Стемы локалитетов (многословные топонимы стеммим по словам)."""
    out: List[str] = []
    for loc in localities:
        for part in re.split(r"[\s\-]+", loc):
            st = make_stem(part)
            if st and st not in out:
                out.append(st)
    return out


def count_matched_localities(text: str, stems: List[str]) -> int:
    """Сколько различных стемов локалитетов встретилось в тексте."""
    low = (text or "").lower()
    return sum(1 for st in stems if st and st in low)


def parse_localities(raw: Optional[str]) -> List[str]:
    """Список локалитетов из строки (разделители: запятая/перевод строки/;)."""
    if not raw:
        return []
    out: List[str] = []
    for p in re.split(r"[,\n;]+", raw):
        p = p.strip()
        if p and p not in out:
            out.append(p)
    return out


def parse_post_refs(text: str) -> Dict[str, set]:
    """Из текста поста вытащить ссылки на сообщества и хэштеги (чистая функция).

    - VK-разметка ``[club123|…]`` / ``[public123|…]`` → ref ``"club123"``.
    - ``@screen_name`` и ``vk.com/<domain>`` → возможный screen_name сообщества
      (пользовательские домены отсеются при резолве через groups.getById).
    - ``#хэштег`` → темы/места (для доп. newsfeed-запросов).

    Возвращает ``{"group_refs": set[str], "hashtags": set[str]}``.
    """
    t = text or ""
    group_refs: set = set()
    for m in re.finditer(r"\[(?:club|public)(\d+)\|", t):
        group_refs.add("club" + m.group(1))
    for m in re.finditer(r"(?:^|[^\w@])@([A-Za-z0-9_]{2,})", t):
        group_refs.add(m.group(1))
    for m in re.finditer(r"vk\.com/([A-Za-z0-9_]+)", t):
        ref = m.group(1)
        if not ref.startswith("wall") and not ref.startswith("id"):
            group_refs.add(ref)
    hashtags: set = set()
    for m in re.finditer(r"#([\wА-Яа-яЁё]+)", t):
        hashtags.add(m.group(1))
    return {"group_refs": group_refs, "hashtags": hashtags}


def extract_repost_owner_ids(wall_items: List[Dict[str, Any]]) -> List[int]:
    """``copy_history[].owner_id < 0`` → положительный id сообщества (чистая)."""
    out: List[int] = []
    for it in wall_items or []:
        for ch in it.get("copy_history") or []:
            oid = ch.get("owner_id")
            if isinstance(oid, int) and oid < 0:
                out.append(abs(oid))
    return out


def add_candidate(seen: Dict[int, Dict[str, Any]], gid: Any, label: str, **fields: Any) -> None:
    """Добавить/слить кандидата в ``seen`` (дедуп по vk_id, мердж ``via``-меток).

    Принимаем только сообщества (положительный gid из groups.search-формы);
    пользователи (для них отдельный пайплайн) игнорируются.
    """
    try:
        gid = int(gid)
    except (TypeError, ValueError):
        return
    if gid <= 0:
        return
    g = seen.get(gid)
    if g is None:
        g = {
            "vk_id": gid,
            "name": (fields.get("name") or "").strip(),
            "screen_name": fields.get("screen_name"),
            "type": fields.get("type"),
            "members_count": fields.get("members_count"),
            "description": fields.get("description"),
            "via": [],
        }
        seen[gid] = g
    if label and label not in g["via"]:
        g["via"].append(label)
    for k in ("name", "screen_name", "members_count", "description"):
        if not g.get(k) and fields.get(k):
            g[k] = fields[k]


def harvest_main_group(token: str, main_group_id: int, posts: int):
    """Стена главной ИНФО-группы района → (repost_group_ids, mention_refs, hashtags).

    Самый высокосигнальный источник для района: кого репостит и упоминает сама
    главная страница. Возвращает положительные id сообществ из репостов,
    строковые refs упоминаний (резолвятся отдельно) и хэштеги.
    """
    resp = _vk_call(
        "wall.get",
        token,
        {"owner_id": -abs(int(main_group_id)), "count": posts, "filter": "owner"},
    )
    items = resp.get("items", []) if isinstance(resp, dict) else []
    repost_ids = sorted(set(extract_repost_owner_ids(items)))
    mention_refs: set = set()
    hashtags: set = set()
    for it in items or []:
        txt = it.get("text") or ""
        for ch in it.get("copy_history") or []:
            txt += "\n" + (ch.get("text") or "")
        refs = parse_post_refs(txt)
        mention_refs |= refs["group_refs"]
        hashtags |= refs["hashtags"]
    _log(
        f"main-group {main_group_id}: {len(items)} posts → "
        f"{len(repost_ids)} reposted groups, {len(mention_refs)} mention refs, "
        f"{len(hashtags)} hashtags"
    )
    return repost_ids, sorted(mention_refs), sorted(hashtags)


def harvest_main_group_links(token: str, main_group_id: int) -> List[str]:
    """Блок «Ссылки» главной группы (``groups.getById fields=links``) → group refs.

    Высокоточный курируемый источник для района: главная ИНФО-страница часто
    прямо линкует партнёрские/сельские/официальные паблики (так оператор обычно
    и собирал пул вручную). Возвращает refs (screen_name) для резолва.
    """
    resp = _vk_call(
        "groups.getById",
        token,
        {"group_id": abs(int(main_group_id)), "fields": "links"},
    )
    groups: List[Dict[str, Any]] = []
    if isinstance(resp, dict) and "groups" in resp:
        groups = resp["groups"] or []
    elif isinstance(resp, list):
        groups = resp
    refs: set = set()
    for g in groups:
        for ln in g.get("links") or []:
            m = re.search(r"vk\.com/([A-Za-z0-9_]+)", ln.get("url") or "")
            if m:
                ref = m.group(1)
                if not ref.startswith("wall") and not ref.startswith("id"):
                    refs.add(ref)
    _log(f"main-group {main_group_id}: links block → {len(refs)} group refs")
    return sorted(refs)


def resolve_group_refs(token: str, refs: List[str], fields: str) -> List[Dict[str, Any]]:
    """groups.getById по смешанным refs (screen_name / ``club123``) → group dicts.

    Пользовательские домены VK молча отсеивает (getById вернёт только сообщества).
    """
    refs = [r for r in refs if r]
    groups: List[Dict[str, Any]] = []
    for i in range(0, len(refs), 200):
        chunk = refs[i : i + 200]
        resp = _vk_call(
            "groups.getById",
            token,
            {"group_ids": ",".join(chunk), "fields": fields},
        )
        if isinstance(resp, dict) and "groups" in resp:
            groups.extend(resp["groups"] or [])
        elif isinstance(resp, list):
            groups.extend(resp)
    return groups


def newsfeed_search(token: str, query: str, count: int, start_time: float) -> set:
    """newsfeed.search по query → set положительных id сообществ из постов.

    Глобальный поиск свежих постов (НЕ wall.search, который только по одной
    стене). Берём ``from_id < 0`` (посты сообществ).
    """
    resp = _vk_call(
        "newsfeed.search",
        token,
        {"q": query, "count": count, "start_time": int(start_time), "extended": 0},
    )
    items = resp.get("items", []) if isinstance(resp, dict) else []
    found: set = set()
    for it in items or []:
        fid = it.get("from_id") or it.get("owner_id") or it.get("source_id")
        if isinstance(fid, int) and fid < 0:
            found.add(abs(fid))
    return found


def crawl_subscriptions(
    token: str, seed_group_ids: List[int], max_seeds: int, max_managers: int
) -> set:
    """Подписки управляющих known-групп → кандидаты (источник ``admin_sub``).

    Для каждой seed-группы: ``groups.getMembers(filter='managers')`` → для каждого
    управляющего ``groups.get(user_id)`` (только открытые профили). Возвращает set
    положительных id сообществ. Жёсткие капы против VK-квоты.
    """
    found: set = set()
    seeds = list(dict.fromkeys(int(abs(g)) for g in seed_group_ids if g))[:max_seeds]
    for gid in seeds:
        resp = _vk_call(
            "groups.getMembers",
            token,
            {"group_id": gid, "filter": "managers", "count": 100},
        )
        items = resp.get("items", []) if isinstance(resp, dict) else []
        mgr_ids: List[int] = []
        for m in items or []:
            uid = m.get("id") if isinstance(m, dict) else m
            if isinstance(uid, int) and uid > 0:
                mgr_ids.append(uid)
        for uid in mgr_ids[:max_managers]:
            gresp = _vk_call("groups.get", token, {"user_id": uid, "extended": 0, "count": 200})
            ids = gresp.get("items", []) if isinstance(gresp, dict) else []
            for x in ids or []:
                if isinstance(x, int) and x > 0:
                    found.add(x)
    _log(f"crawl-subscriptions: {len(seeds)} seeds → {len(found)} candidate groups")
    return found


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
    # --- РАЙОННЫЕ источники (по умолчанию выключены → обратная совместимость) --- #
    ap.add_argument(
        "--main-group",
        type=int,
        default=None,
        help="id главной ИНФО-группы района (со знаком или без). Включает харвест "
        "репостов (copy_history.owner_id) и @упоминаний/ссылок из её постов",
    )
    ap.add_argument(
        "--main-group-posts",
        type=int,
        default=80,
        help="сколько постов главной группы сканировать на репосты/упоминания",
    )
    ap.add_argument(
        "--localities",
        default=None,
        help="сёла/деревни района (через запятую/перевод строки). Включает "
        "локалити-автозапросы groups.search и locality-скоринг кандидатов",
    )
    ap.add_argument(
        "--localities-file",
        default=None,
        help="файл со списком локалитетов (одна на строку); альтернатива --localities",
    )
    ap.add_argument(
        "--newsfeed-search",
        action="store_true",
        help="глобальный newsfeed.search по локалитетам (+ хэштегам главной) за "
        "окно --days; ловит свежие посты сообществ, которые groups.search пропустил",
    )
    ap.add_argument("--newsfeed-count", type=int, default=80, help="постов на newsfeed-запрос")
    ap.add_argument("--days", type=int, default=30, help="окно newsfeed.search в днях")
    ap.add_argument(
        "--crawl-subscriptions",
        action="store_true",
        help="краулинг подписок управляющих (groups.getMembers managers → groups.get). "
        "Тяжёлый источник, жёсткие капы; только открытые профили",
    )
    ap.add_argument("--crawl-max-seeds", type=int, default=8, help="макс. seed-групп для краулинга")
    ap.add_argument(
        "--crawl-max-managers", type=int, default=8, help="макс. управляющих на seed-группу"
    )
    args = ap.parse_args()

    if args.localities_file and not args.localities:
        with open(args.localities_file, encoding="utf-8") as f:
            args.localities = f.read()

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

    locs = parse_localities(args.localities)

    # --- РАЙОННЫЕ источники (опц.) — вливаются в общий seen с via-метками ---- #
    # (1) Локалити-автозапросы: groups.search по каждому селу/деревне района.
    if locs:
        for loc in locs:
            items = search_groups(token, loc, args.count, args.city_id)
            _log(f"loc-search {loc!r}: {len(items)} hits")
            for it in items:
                if it.get("is_closed"):
                    continue
                add_candidate(
                    seen,
                    it.get("id") or 0,
                    f"loc:{loc}",
                    name=(it.get("name") or "").strip(),
                    screen_name=it.get("screen_name"),
                    type=it.get("type"),
                )

    # (2) Репосты + @упоминания со стены главной ИНФО-группы района.
    main_group_hashtags: List[str] = []
    if args.main_group:
        repost_ids, mention_refs, hashtags = harvest_main_group(
            token, args.main_group, args.main_group_posts
        )
        for gid in repost_ids:
            add_candidate(seen, gid, "info_repost")
        if mention_refs:
            for g in resolve_group_refs(
                token, mention_refs, "members_count,description,screen_name,photo_200"
            ):
                add_candidate(
                    seen,
                    g.get("id") or 0,
                    "mention",
                    name=(g.get("name") or "").strip(),
                    screen_name=g.get("screen_name"),
                    members_count=g.get("members_count"),
                    description=g.get("description"),
                )
        # блок «Ссылки» главной группы — высокоточный курируемый источник.
        link_refs = harvest_main_group_links(token, args.main_group)
        if link_refs:
            for g in resolve_group_refs(
                token, link_refs, "members_count,description,screen_name,photo_200"
            ):
                add_candidate(
                    seen,
                    g.get("id") or 0,
                    "link",
                    name=(g.get("name") or "").strip(),
                    screen_name=g.get("screen_name"),
                    members_count=g.get("members_count"),
                    description=g.get("description"),
                )
        main_group_hashtags = hashtags

    # (3) newsfeed.search по локалитетам (+ длинным хэштегам главной) за --days.
    if args.newsfeed_search:
        terms = list(locs) + [h for h in main_group_hashtags if len(h) >= 4]
        start_time = time.time() - args.days * 86400
        nf_total = 0
        for term in terms:
            ids = newsfeed_search(token, term, args.newsfeed_count, start_time)
            nf_total += len(ids)
            for gid in ids:
                add_candidate(seen, gid, "newsfeed")
        _log(f"newsfeed.search: {len(terms)} terms → {nf_total} group hits")

    # (4) Краулинг подписок управляющих known-групп (тяжёлый, капы).
    if args.crawl_subscriptions:
        seeds: List[int] = []
        if args.main_group:
            seeds.append(abs(int(args.main_group)))
        seeds += list(seen.keys())
        for gid in crawl_subscriptions(token, seeds, args.crawl_max_seeds, args.crawl_max_managers):
            add_candidate(seen, gid, "admin_sub")

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

    # 2a. locality-скоринг: сколько различных стемов сёл/деревень района
    # встречается в name+description кандидата (для района — главный сигнал).
    loc_stems = locality_stems(locs)
    if loc_stems:
        for g in seen.values():
            hay = " ".join(filter(None, [g.get("name"), g.get("description")]))
            g["matched_localities"] = count_matched_localities(hay, loc_stems)

    # 2b. region-filter (опц.): отсекаем чужие регионы по name+description.
    # VK groups.search для запросов вида «Министерство X области» fuzzy-матчит
    # аналоги других регионов — этот фильтр их режет автоматически.
    if args.region_filter:
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
    # При заданных локалитетах первичный ключ — matched_localities (район: мелкий
    # сельский клуб с упоминанием села важнее общегородского гиганта).
    def _rank_key(g: Dict[str, Any]):
        return (g.get("matched_localities") or 0, g.get("members_count") or 0)

    eligible = [g for g in seen.values() if (g.get("members_count") or 0) >= args.min_members]
    if args.per_label_top > 0:
        by_label: Dict[str, List[Dict[str, Any]]] = {}
        for g in eligible:
            for lbl in g.get("via", []) or ["?"]:
                by_label.setdefault(lbl, []).append(g)
        selected_ids: set[int] = set()
        selected: List[Dict[str, Any]] = []
        for lbl in sorted(by_label):
            gs = sorted(by_label[lbl], key=_rank_key, reverse=True)
            for g in gs[: args.per_label_top]:
                if g["vk_id"] not in selected_ids:
                    selected_ids.add(g["vk_id"])
                    selected.append(g)
        selected.sort(key=_rank_key, reverse=True)
        top = selected[: args.top]
        _log(
            f"{len(eligible)} groups >= {args.min_members} members; "
            f"per-label top {args.per_label_top} → {len(selected)} selected; "
            f"fetching posts for top {len(top)}"
        )
    else:
        eligible.sort(key=_rank_key, reverse=True)
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

    # сводка по источникам (префикс via-метки): geo/kw vs новые районные источники
    src_counter: Counter = Counter()
    for g in seen.values():
        for v in g.get("via", []) or []:
            src_counter[v.split(":")[0]] += 1

    # компактный полный список всех найденных (без постов) — для анализа покрытия
    # и recall (top-N с постами идёт отдельно в "candidates").
    all_found = [
        {
            "vk_id": g["vk_id"],
            "name": g.get("name"),
            "screen_name": g.get("screen_name"),
            "members_count": g.get("members_count"),
            "matched_localities": g.get("matched_localities", 0),
            "via": g.get("via", []),
        }
        for g in sorted(
            seen.values(),
            key=lambda g: (g.get("matched_localities") or 0, g.get("members_count") or 0),
            reverse=True,
        )
    ]

    out = {
        "scanned_queries": [s["q"] for s in queries],
        "localities": locs,
        "total_unique": len(seen),
        "ranked": len(groups),
        "source_breakdown": dict(src_counter),
        "main_group_hashtags": main_group_hashtags,
        "all_found": all_found,
        "candidates": top,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    _log(f"wrote {len(top)} candidates → {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
