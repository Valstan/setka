"""OpenStreetMap Overpass API resolver — список нп административного района.

Используется UI prepare-страницей: при создании нового региона юзер видит
«suggested» список населённых пунктов района (auto-fill из OSM), может
поправить и сохранить в ``regions.config['localities']``.

Public API: ``fetch_localities(district_name) -> list[str]``.

Defensive: на любые failures (timeout, 5xx, bad JSON, OSM down) возвращает
пустой list. Caller сам решает показать сообщение «OSM недоступен, заполни
вручную через ChatGPT».
"""

from __future__ import annotations

import logging
from typing import List

import requests

logger = logging.getLogger(__name__)

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
DEFAULT_TIMEOUT = 30  # seconds; Overpass иногда уходит в 20+ сек по тяжёлым районам

# Какие OSM place=* считаем «населённым пунктом». Покрывает города/посёлки/сёла/
# деревни/хутора + locality (часто помечают совсем мелкие/несуществующие).
# isolated_dwelling намеренно НЕ берём — это одиночные жилые дома, не нп.
_PLACE_TYPES = (
    "city",
    "town",
    "village",
    "hamlet",
    "locality",
)


def _build_query(district_name: str) -> str:
    """Build Overpass QL query for villages in a district by name.

    Ищем admin_level 5 (район субъекта РФ) и 6 (на всякий случай — некоторые
    регионы помечают районы level=6). Берём `name:ru` приоритетно, fallback
    на `name`. Запрос двусторонне квотирует имя через единственные кавычки.
    """
    safe = district_name.replace("\\", "").replace('"', '\\"')
    placetypes = "|".join(_PLACE_TYPES)
    # NB: admin_level в России для районов чаще 6 (district), но местами 5;
    # берём диапазон 5-6 чтобы не ловить subjects-of-RF (level=4) и города
    # внутри района (level=8).
    return f"""
[out:json][timeout:25];
(
  area["name:ru"="{safe}"]["admin_level"~"^[56]$"];
  area["name"="{safe}"]["admin_level"~"^[56]$"];
)->.searchArea;
(
  node["place"~"^({placetypes})$"](area.searchArea);
);
out tags;
""".strip()


def _extract_names(elements: List[dict]) -> List[str]:
    """Извлекает уникальные имена нп из OSM elements, приоритет name:ru."""
    seen: set[str] = set()
    names: List[str] = []
    for el in elements or []:
        tags = el.get("tags") or {}
        name = (tags.get("name:ru") or tags.get("name") or "").strip()
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        names.append(name)
    return sorted(names)


def fetch_localities(district_name: str, *, timeout: int = DEFAULT_TIMEOUT) -> List[str]:
    """Return list of localities in a given OSM admin district.

    Args:
        district_name: полное название района как в OSM, например
            ``"Тужинский район"``, ``"Малмыжский район"``. Должно совпадать
            с тегом ``name:ru`` или ``name`` admin_level=5/6 relation в OSM.
        timeout: HTTP timeout, по умолчанию 30s (Overpass иногда тормозит).

    Returns:
        Отсортированный list уникальных имён нп. На любые ошибки (timeout,
        5xx, JSON parse, network) — пустой list. Caller должен фоллбечить
        на ручной ввод.
    """
    name = (district_name or "").strip()
    if not name:
        return []

    query = _build_query(name)
    try:
        resp = requests.post(
            OVERPASS_URL,
            data={"data": query},
            timeout=timeout,
            headers={"User-Agent": "setka-discovery/1.0"},
        )
    except requests.RequestException as e:
        logger.info("osm_overpass: request failed for %r: %s", name, e)
        return []

    if resp.status_code != 200:
        logger.info(
            "osm_overpass: non-200 for %r: %s %s",
            name,
            resp.status_code,
            (resp.text or "")[:200],
        )
        return []

    try:
        data = resp.json()
    except ValueError as e:
        logger.info("osm_overpass: bad JSON for %r: %s", name, e)
        return []

    return _extract_names(data.get("elements") or [])
