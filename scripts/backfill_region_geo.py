"""One-off + идемпотентно: геокодировать центры регионов в ``Region.config['geo']``.

PR2 «автоопределение гео-соседей»: соседи подсказываются по близости центров,
поэтому у каждого региона должны быть координаты в ``config['geo']``. Endpoint
``GET /api/regions/suggest-neighbors`` геокодит только цель (1 запрос), а
координаты остальных регионов читает из кэша — заполняет их этот скрипт.

Источник координат — OSM Nominatim (usage policy ≤ 1 req/s, троттлинг ниже).
Лейбл выводится из ``center_city`` / ``name`` тем же ``_geocodable_label``, что и
endpoint, — единый источник правды.

  * dry-run (по умолчанию): печатает ``code → label → (lat,lon) | NOT FOUND`` и
    ничего не пишет;
  * ``--apply``: записывает ``config['geo'] = {lat, lon, label, source, geocoded_at}``;
  * ``--force``: перегеокодировать даже уже закэшированные регионы.

Идемпотентно: повторный ``--apply`` без ``--force`` пропускает уже закэшированные.
**Урок PR1: всегда сначала dry-run, проверить мелкие районы, потом --apply.**

Запуск на проде:
    ssh setka 'cd /home/valstan/SETKA && ./venv/bin/python scripts/backfill_region_geo.py'
    ssh setka 'cd /home/valstan/SETKA && ./venv/bin/python scripts/backfill_region_geo.py --apply'
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime

from sqlalchemy import select

from database.connection import AsyncSessionLocal
from database.models import Region
from modules.geo.geocoder import geocode
from web.api.regions import _geocodable_label

# Nominatim usage policy: не чаще ~1 запроса в секунду.
NOMINATIM_MIN_INTERVAL_S = 1.1


async def _run(apply: bool, force: bool) -> None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Region))
        regions = list(result.scalars().all())
        by_id = {r.id: r for r in regions}

        changed = 0
        for r in regions:
            cfg = r.config if isinstance(r.config, dict) else {}
            cached = cfg.get("geo")
            has_cache = isinstance(cached, dict) and "lat" in cached and "lon" in cached
            if has_cache and not force:
                print(f"{r.code:16} cached {cached.get('lat')},{cached.get('lon')} (skip)")
                continue

            label = _geocodable_label(r.name, r.center_city)
            if not label:
                print(f"{r.code:16} NO LABEL (center_city и name пусты) — skip")
                continue

            parent = by_id.get(r.parent_region_id) if r.parent_region_id else None
            hint = _geocodable_label(parent.name, parent.center_city) if parent else None
            coords = await geocode(label, region_hint=hint)
            await asyncio.sleep(NOMINATIM_MIN_INTERVAL_S)
            if not coords:
                print(f"{r.code:16} '{label}' -> NOT FOUND")
                continue

            print(f"{r.code:16} '{label}' -> {coords[0]:.5f},{coords[1]:.5f}")
            changed += 1
            if apply:
                cfg = dict(cfg)
                cfg["geo"] = {
                    "lat": coords[0],
                    "lon": coords[1],
                    "label": label,
                    "source": "nominatim",
                    "geocoded_at": datetime.utcnow().isoformat(),
                }
                r.config = cfg

        if apply:
            await session.commit()
            print(f"\nApplied: {changed} region(s) geocoded.")
        else:
            print(
                f"\nDry-run: {changed} region(s) would be geocoded. Re-run with --apply to write."
            )


def main() -> None:
    ap = argparse.ArgumentParser(description="Backfill Region.config['geo'] via OSM Nominatim")
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry-run)")
    ap.add_argument("--force", action="store_true", help="re-geocode even already-cached regions")
    args = ap.parse_args()
    asyncio.run(_run(args.apply, args.force))


if __name__ == "__main__":
    main()
