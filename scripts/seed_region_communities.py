#!/usr/bin/env python3
"""Idempotent seed/upsert of communities into a region pool from a JSON file.

Часть discovery-скила: после ручной нейро-классификации (discover_scan.py +
анализ постов оператором) утверждённые сообщества записываются в пул региона.

JSON-формат (список):
    [{"vk_id": 60609780, "category": "novost",
      "name": "...", "screen_name": "zlo43"}, ...]

``vk_id`` подаётся ПОЛОЖИТЕЛЬНЫМ (как из groups.search) — в БД пишется со
знаком минус (owner_id-форма, как уже хранятся communities). Дедуп по
(region_id, abs(vk_id), category): повторный запуск ничего не дублирует.

Запуск на проде (с окружением, секреты не печатаются)::

    sudo bash -c 'set -a; . /etc/setka/setka.env; set +a; \
      cd /home/valstan/SETKA && \
      ./venv/bin/python /tmp/seed_region_communities.py \
        --region-code kirov_obl --file /tmp/_seed.json'

Сначала прогнать с ``--dry-run``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter


async def _run(region_code: str, path: str, dry_run: bool) -> int:
    from sqlalchemy import select

    from database.connection import AsyncSessionLocal
    from database.models import Community, Region

    with open(path, encoding="utf-8") as f:
        items = json.load(f)

    async with AsyncSessionLocal() as session:
        res = await session.execute(select(Region).where(Region.code == region_code))
        region = res.scalars().first()
        if not region:
            print(f"FATAL: region {region_code!r} not found", file=sys.stderr)
            return 2
        rid = region.id

        existing = await session.execute(
            select(Community.vk_id, Community.category).where(Community.region_id == rid)
        )
        seen = {(abs(int(vid)), cat) for vid, cat in existing.all()}

        inserted = Counter()
        skipped = 0
        for it in items:
            try:
                vk_abs = abs(int(it["vk_id"]))
                category = str(it["category"]).strip()
                name = str(it["name"]).strip()
            except (KeyError, TypeError, ValueError):
                print(f"  ! bad item, skipped: {it}", file=sys.stderr)
                continue
            screen_name = it.get("screen_name") or None
            if (vk_abs, category) in seen:
                skipped += 1
                continue
            seen.add((vk_abs, category))
            inserted[category] += 1
            if not dry_run:
                session.add(
                    Community(
                        region_id=rid,
                        vk_id=-vk_abs,
                        name=name,
                        screen_name=screen_name,
                        category=category,
                        is_active=True,
                    )
                )

        if not dry_run:
            await session.commit()

        total = sum(inserted.values())
        mode = "DRY-RUN (nothing written)" if dry_run else "WRITTEN"
        print(f"[{mode}] region={region_code} (id={rid})")
        for cat, n in sorted(inserted.items()):
            print(f"  +{n:2d}  {cat}")
        print(f"  total inserted: {total}; skipped (already present): {skipped}")
        return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Seed communities into a region pool")
    ap.add_argument("--region-code", required=True)
    ap.add_argument("--file", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    return asyncio.run(_run(args.region_code, args.file, args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
