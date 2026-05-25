"""Composite VK groups discovery for a region.

Strategy (2026-05-25 rework: localities-driven, см. PR feat/discovery-localities-relevance):

1. **Geo search** — ``groups.search(q="<center_city>", city_id=<vk_city_id>)``.
2. **Localities search** — для каждого нп из ``region.config['localities']``
   шлём ``groups.search(q="<нп>")`` без city_id. Это даёт мелкие районные
   паблики, которые VK не привязывает к городу-центру.
3. **Keyword search** — для каждого ключевика из ``region.config['discovery_keywords']``
   (или fallback ``CATEGORY_KEYWORDS``) шлём ``groups.search(q="<center_city> <keyword>")``.
4. **Dedup** — по ``id``. ``discovered_via`` фиксирует первый источник.
5. **Enrichment** — один ``groups.getById(group_ids=…, fields=…)`` для всех
   уникальных id.
6. **Hard relevance filter** — если ``localities`` непуст, отбрасываем
   кандидатов, у которых ни один локалитет (по corner-стему) не встречается
   в ``name + description``. Это режет 90% мусора без AI: общегородские/
   областные паблики не пройдут.
7. **Sort** — ``(matched_localities_count desc, members_count desc)`` — паблик
   с тремя топонимами района обгонит «Море Парк Киров» с 90k подписчиками.

Recent posts (``wall.get`` per group) тянет уже async-обвязка
``tasks.discovery_tasks._ai_categorize_all``.

Возвращает список ``DiscoveredGroup`` — готовые к AI-категоризации и
upsert'у в ``community_candidates``.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from modules.vk_monitor.vk_client import VKClient

logger = logging.getLogger(__name__)

# Fallback-ключевики на категорию, когда `region.config['discovery_keywords']`
# пуст. На вход VK groups.search идёт `<center_city> <kw>`.
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

# Минимальная длина корня после стема (защита от слишком коротких корней
# вроде «У» из «Уя» — они дают много false-positive).
_STEM_MIN_LEN = 3

# Гласные русского алфавита — отбрасываем конечные гласные для упрощённого
# стема. Покрывает большинство склонений: «Тужа» → «туж», «Шешурга» →
# «шешург», «Михайловское» → «михайловск», «Тужи»/«Тужу»/«Туже» → «туж».
_RU_VOWELS = "аеёиоуыэюя"


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
    recent_posts: List[str] = field(default_factory=list)
    # Сколько локалитетов района нашлось в name+description. Используется
    # для сортировки и debug-логирования; в БД не пишется.
    matched_localities: int = 0


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


def _make_stem(name: str) -> str:
    """Стем для substring-матчинга по корню русского топонима.

    Не настоящий морфологический стем (нет pymorphy2 в зависимостях) —
    наивное отбрасывание конечных гласных. Покрывает основные русские
    склонения и прилагательные:

    - «Тужа» → «туж» (матчит «Тужа», «Тужи», «Тужу», «Тужинский»);
    - «Шешурга» → «шешург»;
    - «Михайловское» → «михайловск»;
    - «Уя» → «уя» (короткое — не трогаем).

    Конечные согласные не отбрасываем (для «Малмыж» оставляем «малмыж» —
    он сам по себе матчит «Малмыжский», «Малмыже» как substring).
    """
    s = (name or "").strip().lower()
    while len(s) > _STEM_MIN_LEN and s[-1] in _RU_VOWELS:
        s = s[:-1]
    return s


def _count_localities_in_text(stems: List[str], text: str) -> int:
    """Считает, сколько локалитетов района встречается в тексте.

    Использует regex с word-boundary слева (`\\b<стем>`), чтобы не матчить
    «батужа» при стеме «туж». Справа word-boundary нет — иначе пропустим
    склонения («Тужинский», «Тужанский»).
    """
    if not stems or not text:
        return 0
    haystack = text.lower()
    count = 0
    for st in stems:
        if not st:
            continue
        # Левый word-boundary через regex (учитываем кириллицу через \w).
        pattern = r"\b" + re.escape(st)
        if re.search(pattern, haystack):
            count += 1
    return count


def _build_search_plan(
    *,
    center_city: str,
    localities: Sequence[str],
    keywords: Sequence[str],
) -> List[tuple[str, str, bool]]:
    """Return list of (query, source_label, use_city_id) tuples.

    Порядок (важен для discovered_via — первый источник побеждает в дедупе):
    1. geo_search — `<center_city>` с city_id (если есть).
    2. loc:<нп> — каждый локалитет района, без city_id.
    3. kw:<keyword> — `<center_city> <keyword>`, без city_id.
       Если keywords пуст — берётся flat-список из CATEGORY_KEYWORDS.
    """
    plan: List[tuple[str, str, bool]] = []
    if center_city:
        plan.append((center_city, "geo_search", True))

    seen_loc: set[str] = set()
    for loc in localities:
        q = (loc or "").strip()
        if not q or q.lower() in seen_loc:
            continue
        seen_loc.add(q.lower())
        plan.append((q, f"loc:{q}", False))

    if keywords:
        kw_source = list(keywords)
    else:
        kw_source = [kw for kws in CATEGORY_KEYWORDS.values() for kw in kws]

    seen_kw: set[str] = set()
    for kw in kw_source:
        kw_norm = (kw or "").strip()
        if not kw_norm or kw_norm.lower() in seen_kw:
            continue
        seen_kw.add(kw_norm.lower())
        q = f"{center_city} {kw_norm}".strip() if center_city else kw_norm
        plan.append((q, f"kw:{kw_norm}", False))

    return plan


DEFAULT_MAX_CANDIDATES = 150


def discover_for_region(
    *,
    client: VKClient,
    center_city: str,
    vk_city_id: Optional[int] = None,
    localities: Optional[Sequence[str]] = None,
    keywords: Optional[Sequence[str]] = None,
    per_query_count: int = 100,
    exclude_vk_ids: Optional[Sequence[int]] = None,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
) -> List[DiscoveredGroup]:
    """Run composite discovery.

    Args:
        client: VKClient (already initialised with a parse-token).
        center_city: human-readable name of the region centre, e.g. "Малмыж".
            Используется для geo-search и как префикс для keyword-запросов.
        vk_city_id: optional numeric VK city_id. If given, geo-search uses it.
            Локалитеты и keyword-search всегда без city_id (мелкие районные
            паблики часто помечены городом-центром района, не каждым нп).
        localities: список нп района (Тужа, Шешурга, Михайловское, ...).
            Используются и как поисковые запросы, и как **жёсткий фильтр**
            релевантности — кандидат без ни одного нп в name/description
            отбрасывается.
        keywords: кастомные тематические ключевики из ``region.config``.
            Если пусто — fallback на flat-список ``CATEGORY_KEYWORDS``.
        per_query_count: how many results to ask VK per call.
        exclude_vk_ids: positive ids (уже добавленные communities + rejected).
        max_candidates: top-N после relevance-фильтра и сортировки.

    Returns:
        Список ``DiscoveredGroup``, отсортированный по
        ``(matched_localities desc, members_count desc)``.
    """
    if not (center_city or "").strip() and not (localities or []):
        return []

    loc_list = [loc for loc in (localities or []) if (loc or "").strip()]
    kw_list = [kw for kw in (keywords or []) if (kw or "").strip()]
    plan = _build_search_plan(
        center_city=center_city or "",
        localities=loc_list,
        keywords=kw_list,
    )
    exclude_set = {abs(int(v)) for v in (exclude_vk_ids or [])}

    # Step 1+2+3: search.
    seen: Dict[int, DiscoveredGroup] = {}
    for query, source, use_city_id in plan:
        try:
            city_id_for_call = vk_city_id if use_city_id else None
            items = client.search_groups(
                query=query,
                city_id=city_id_for_call,
                count=per_query_count,
            )
        except Exception as e:  # extra safety
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

    # Step 4: enrichment one-shot.
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

    groups = list(seen.values())

    # Step 5: hard relevance filter + matched-count для сортировки.
    # Если localities заданы — отбрасываем кандидатов без ни одного локалитета
    # в name+description. Если localities пусты — фильтр пропускаем (старое
    # поведение для backwards-compat с регионами, у которых config не заполнен).
    if loc_list:
        stems = [_make_stem(loc) for loc in loc_list]
        before = len(groups)
        kept: List[DiscoveredGroup] = []
        for g in groups:
            text = " ".join(filter(None, [g.name, g.description])).strip()
            matched = _count_localities_in_text(stems, text)
            g.matched_localities = matched
            if matched > 0:
                kept.append(g)
        groups = kept
        logger.info(
            "discovery: relevance filter — %s/%s candidates kept (had localities=%d)",
            len(groups),
            before,
            len(loc_list),
        )

    if not groups:
        return []

    # Step 6: sort. Сначала по matched_localities (если был фильтр), потом
    # по members_count (информативный сигнал — мёртвые мелкие паблики
    # модератору не интересны).
    groups.sort(
        key=lambda g: (g.matched_localities, g.members_count or 0),
        reverse=True,
    )

    if len(groups) > max_candidates:
        logger.info(
            "discovery: %s candidates → truncated to top-%s",
            len(groups),
            max_candidates,
        )
        groups = groups[:max_candidates]

    return groups
