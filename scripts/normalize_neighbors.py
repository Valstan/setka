"""One-off: нормализовать ``Region.neighbors`` → коды + сделать связь обоюдной.

Историческая проблема (обнаружено 2026-05-29): часть ``Region.neighbors`` забита
русскими названиями / кириллицей («кукмор», «балтаси», «малмыж»), а движок
соседского обмена (``modules.cascaded_bulletin.run_neighbor_bulletin``) матчит соседей
по ``Region.code.in_(codes)`` — из-за чего обмен молча не находил ни одного соседа.

Скрипт:
  1. Резолвит каждый токен neighbors в код региона (по коду / name / center_city).
  2. Отбрасывает неизвестные токены и само-соседа.
  3. Делает граф соседей симметричным (если A→B, то и B→A — обоюдность).
  4. По умолчанию dry-run (только печать диффа); с ``--apply`` записывает в БД.

Идемпотентно: повторный запуск после ``--apply`` покажет «0 region(s) would change».

Запуск на проде:
    ssh setka 'cd /home/valstan/SETKA && ./venv/bin/python scripts/normalize_neighbors.py'
    ssh setka 'cd /home/valstan/SETKA && ./venv/bin/python scripts/normalize_neighbors.py --apply'
"""

from __future__ import annotations

import argparse
import asyncio
from typing import Dict, Set

from sqlalchemy import select

from database.connection import AsyncSessionLocal
from database.models import Region
from web.api.regions import _normalize_neighbor_codes, _parse_neighbor_tokens


async def _run(apply: bool) -> None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Region))
        regions = list(result.scalars().all())

        # 1-2. Нормализуем neighbors каждого региона в валидные коды.
        normalized: Dict[str, Set[str]] = {}
        for r in regions:
            codes = await _normalize_neighbor_codes(session, r.neighbors, r.code)
            normalized[r.code] = set(codes)

        # 3. Симметризация (union): если A назвал B соседом — B тоже получает A.
        codes_set = {r.code for r in regions}
        for a, neigh in list(normalized.items()):
            for b in list(neigh):
                if b in codes_set:
                    normalized[b].add(a)

        # 4. Печать диффа / применение.
        changed = 0
        for r in regions:
            old = ",".join(sorted(_parse_neighbor_tokens(r.neighbors)))
            new = ",".join(sorted(normalized.get(r.code, set())))
            if old != new:
                changed += 1
                print(f"{r.code:16} '{r.neighbors or ''}' -> '{new or ''}'")
                if apply:
                    r.neighbors = new or None

        if apply:
            await session.commit()
            print(f"\nApplied: {changed} region(s) updated.")
        else:
            print(f"\nDry-run: {changed} region(s) would change. Re-run with --apply to write.")


def main() -> None:
    ap = argparse.ArgumentParser(description="Normalize + symmetrize Region.neighbors")
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry-run)")
    args = ap.parse_args()
    asyncio.run(_run(args.apply))


if __name__ == "__main__":
    main()
