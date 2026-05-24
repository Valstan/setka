"""Composite VK groups discovery for a region.

Strategy:

1. **Geo search** — ``groups.search(q="<center_city>", city_id=<vk_city_id>)``.
2. **Keyword search** — для каждой темы из ``CATEGORY_KEYWORDS`` шлём
   ``groups.search(q="<center_city> <keyword>")``.
3. **Dedup** — по ``id``. ``discovered_via`` фиксирует первый источник,
   в котором кандидат всплыл (для дебага).
4. **Enrichment** — один ``groups.getById(group_ids=…, fields=…)`` для
   всех уникальных id, чтобы получить ``description``, ``members_count``,
   ``screen_name``, ``photo_200`` без отдельных запросов на каждую группу.

Recent posts (``wall.get`` per group) тянет уже async-обвязка
``tasks.discovery_tasks._ai_categorize_all`` через ``asyncio.to_thread`` +
semaphore, чтобы event-loop оставался свободен для параллельных Groq
вызовов и uvicorn keep-alive не лопался на 100+ группах.

Возвращает список ``DiscoveredGroup`` — готовые к AI-категоризации и
upsert'у в ``community_candidates``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from modules.vk_monitor.vk_client import VKClient

logger = logging.getLogger(__name__)

# Ключевики на категорию. На вход VK groups.search идёт `<center_city> <kw>`.
# Сами категории — те же, что в `Community.category` и beat schedule.
CATEGORY_KEYWORDS: Dict[str, List[str]] = {
    "novost": ["новости", "инфо", "вести"],
    "reklama": ["объявления", "доска", "барахолка", "куплю продам"],
    "sosed": ["соседи", "ДТП", "происшествия"],
    "kultura": ["культура", "афиша", "дом культуры"],
    "sport": ["спорт", "фитнес"],
    "admin": ["администрация", "район", "город"],
    "detsad": ["детский сад", "школа", "родители"],
}

# Какие fields'ы запрашиваем у groups.getById для enrichment.
_ENRICH_FIELDS = "description,members_count,activity,status,screen_name,photo_200"


@dataclass
class DiscoveredGroup:
    """Snapshot VK-группы на момент discovery — то, что попадёт в
    ``community_candidates``."""

    vk_id: int
    name: str
    screen_name: Optional[str] = None
    photo_url: Optional[str] = None
    description: Optional[str] = None
    members_count: Optional[int] = None
    discovered_via: str = ""
    # Последние посты (текст) — для ai_categorizer'а; не хранится в БД.
    # Заполняется в `discover_for_region` через `client.get_wall_posts`.
    recent_posts: List[str] = field(default_factory=list)


def _normalize_search_item(item: Dict) -> DiscoveredGroup:
    """Convert raw groups.search/groups.getById item to DiscoveredGroup."""
    return DiscoveredGroup(
        vk_id=int(item.get("id") or 0),
        name=(item.get("name") or "").strip(),
        screen_name=item.get("screen_name") or None,
        photo_url=item.get("photo_200") or None,
        description=(item.get("description") or "").strip() or None,
        members_count=item.get("members_count"),
    )


def _build_search_plan(center_city: str, categories: Sequence[str]) -> List[tuple[str, str]]:
    """Return list of (query, source_label) tuples to feed into groups.search.

    Out-of-band test contract: первый элемент — чистый geo-search (только город),
    остальные — keyword-вариации. Менять порядок осторожно: в дедупе важна
    стабильность `discovered_via` (первый найденный источник побеждает).
    """
    plan: List[tuple[str, str]] = [(center_city, "geo_search")]
    seen_kws: set[str] = set()
    for cat in categories:
        for kw in CATEGORY_KEYWORDS.get(cat, []):
            if kw in seen_kws:
                continue
            seen_kws.add(kw)
            plan.append((f"{center_city} {kw}", f"kw:{kw}"))
    return plan


def discover_for_region(
    *,
    client: VKClient,
    center_city: str,
    vk_city_id: Optional[int] = None,
    categories: Optional[Sequence[str]] = None,
    per_query_count: int = 100,
    exclude_vk_ids: Optional[Sequence[int]] = None,
) -> List[DiscoveredGroup]:
    """Run composite discovery.

    Args:
        client: VKClient (already initialised with a parse-token).
        center_city: human-readable name of the region centre, e.g. "Малмыж".
        vk_city_id: optional numeric VK city_id. If given, geo-search uses it
            to narrow results. Keyword searches stay city-agnostic on purpose
            (we want to catch e.g. районный паблик «<Город> Новости», который
            может быть зарегистрирован «в Кирове», but уже relevant).
        categories: subset of ``CATEGORY_KEYWORDS`` keys to search. Default
            is all of them. Useful to limit cost during dev.
        per_query_count: how many results to ask VK per call.
        exclude_vk_ids: positive ids that должны быть отфильтрованы (уже
            добавленные communities + кандидаты, которые модератор когда-то
            отверг). Сравнение по abs().

    Returns:
        Список уникальных ``DiscoveredGroup`` с заполненными description /
        members_count / etc. Если поиск ничего не дал, возвращается ``[]``.
    """
    if not (center_city or "").strip():
        return []

    cats = list(categories) if categories else list(CATEGORY_KEYWORDS.keys())
    plan = _build_search_plan(center_city, cats)
    exclude_set = {abs(int(v)) for v in (exclude_vk_ids or [])}

    # Step 1+2: search.
    seen: Dict[int, DiscoveredGroup] = {}
    for query, source in plan:
        try:
            city_id_for_call = vk_city_id if source == "geo_search" else None
            items = client.search_groups(
                query=query,
                city_id=city_id_for_call,
                count=per_query_count,
            )
        except Exception as e:  # extra safety — search_groups уже глотает API errors
            logger.warning("discovery: search_groups failed for %r: %s", query, e)
            continue
        for it in items:
            gid = int(it.get("id") or 0)
            if not gid or gid in seen or gid in exclude_set:
                continue
            g = _normalize_search_item(it)
            g.discovered_via = source
            seen[gid] = g

    if not seen:
        return []

    # Step 3: enrichment one-shot.
    ids = list(seen.keys())
    try:
        enriched = client.get_groups_by_ids(ids, fields=_ENRICH_FIELDS)
    except Exception as e:
        logger.warning("discovery: enrichment failed: %s", e)
        enriched = []

    for it in enriched:
        gid = int(it.get("id") or 0)
        g = seen.get(gid)
        if g is None:
            continue
        # Don't overwrite name / screen_name if enrichment returned blanks.
        if it.get("name"):
            g.name = (it["name"] or "").strip() or g.name
        if it.get("screen_name"):
            g.screen_name = it["screen_name"]
        if it.get("photo_200"):
            g.photo_url = it["photo_200"]
        if it.get("description"):
            g.description = (it["description"] or "").strip() or g.description
        if it.get("members_count") is not None:
            g.members_count = it["members_count"]

    # Recent posts заполняются НЕ здесь, а в `_ai_categorize_all` (async, с
    # bounded concurrency через Semaphore). Sync-цикл по 100 группам в этой
    # функции упирался в `VKClient.GLOBAL_PARSE_INTERVAL_SECONDS=0.4`
    # serializing → 40+ секунд просто на wall.get, а total discovery
    # с AI на одного worker'а превышал uvicorn keep-alive (~120s, hang в UI).
    # См. PR #32: fetch постов параллелизован через `to_thread` + semaphore=8.

    return list(seen.values())
