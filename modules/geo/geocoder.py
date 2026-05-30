"""Геокодинг центров регионов через OSM Nominatim + расстояние haversine.

Используется PR2 «автоопределение гео-соседей»: координаты центра региона
кэшируются в ``Region.config['geo']``, соседи подсказываются по близости центров
(``web/api/regions.py:suggest_neighbors`` + ``scripts/backfill_region_geo.py``).

Nominatim usage policy требует:
  * осмысленный ``User-Agent`` с контактом/назначением;
  * не более ~1 запроса в секунду — троттлинг на стороне вызывающего
    (backfill-скрипт делает ``asyncio.sleep`` между запросами).

Точечный геокодинг известного центра («Тужа», «Малмыж») надёжен — в отличие от
area-enumeration через Overpass, удалённого 2026-05-25 (не находил мелкие районы,
clipboard-prompt оказался лучше). Здесь другой API и другая задача: имя → точка.
"""

from __future__ import annotations

import logging
import math
from typing import Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "setka-geo/1.0 (https://github.com/Valstan/setka; VK regional news aggregator)"
DEFAULT_TIMEOUT = 12.0
EARTH_RADIUS_KM = 6371.0

# (lat, lon)
Coords = Tuple[float, float]


async def geocode(
    label: str, *, region_hint: Optional[str] = None, country_bias: str = "Россия"
) -> Optional[Coords]:
    """Геокодировать имя центра региона в ``(lat, lon)`` через Nominatim.

    ``region_hint`` (имя родительской области, напр. «Кировская область») и
    ``country_bias`` («Россия») добавляются к запросу, если их ещё нет в строке —
    это дизамбигуирует омонимы (Советск есть и в Кировской, и в Калининградской
    области; Лебяжье — во многих регионах). Без hint bare-name геокод промахивался.

    Возвращает ``None`` при пустом вводе, сетевой ошибке или пустом ответе —
    вызывающий трактует ``None`` как «координаты определить не удалось».
    """
    if not label or not label.strip():
        return None
    query = label.strip()
    if region_hint and region_hint.strip() and region_hint.strip().lower() not in query.lower():
        query = f"{query}, {region_hint.strip()}"
    if country_bias and country_bias.lower() not in query.lower():
        query = f"{query}, {country_bias}"

    params = {"q": query, "format": "json", "limit": 1, "accept-language": "ru"}
    headers = {"User-Agent": USER_AGENT}
    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.get(NOMINATIM_URL, params=params, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("geocode(%r) failed: %s", label, exc)
        return None

    if not data:
        logger.info("geocode(%r): Nominatim returned no result", label)
        return None
    try:
        return (float(data[0]["lat"]), float(data[0]["lon"]))
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        logger.warning("geocode(%r): unexpected payload: %s", label, exc)
        return None


def haversine_km(a: Coords, b: Coords) -> float:
    """Great-circle расстояние между двумя точками ``(lat, lon)`` в километрах."""
    lat1, lon1 = a
    lat2, lon2 = b
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    h = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(h)))
