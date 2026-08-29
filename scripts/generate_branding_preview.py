"""Превью фирменной графики всех регионов — приёмка владельцем ДО заливки.

Генерит аватар и обложку каждого активного района в каталог (по умолчанию
``branding_preview/`` рядом со скриптом) и печатает список. Ничего не заливает
и к ВК не ходит; БД нужна только за списком регионов, при недоступности можно
передать ``--offline`` с кодами через запятую.

Запуск:
    python scripts/generate_branding_preview.py --all --out /tmp/branding
    python scripts/generate_branding_preview.py --offline oparino,uni --out /tmp/b
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def load_regions() -> list:
    from sqlalchemy import text

    from database.connection import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT code, name FROM regions "
                    "WHERE is_active IS TRUE AND vk_group_id IS NOT NULL AND kind = 'raion' "
                    "ORDER BY code"
                )
            )
        ).fetchall()
    return [(r.code, r.name) for r in rows]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", help="все активные районы из БД")
    parser.add_argument("--offline", default=None, help="коды через запятую, без БД")
    parser.add_argument("--out", default=None, help="каталог вывода")
    args = parser.parse_args()

    from modules.promotion.branding import default_tagline, render_avatar, render_cover
    from modules.region_links import base_title

    out_dir = args.out or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "branding_preview"
    )
    os.makedirs(out_dir, exist_ok=True)

    if args.offline:
        regions = [(code.strip(), code.strip().title()) for code in args.offline.split(",")]
    elif args.all:
        from modules.secrets_bootstrap import bootstrap_secrets

        bootstrap_secrets()
        regions = asyncio.run(load_regions())
    else:
        parser.error("нужен --all или --offline code1,code2")
        return 2

    for code, name in regions:
        title = base_title(name, None)
        avatar = render_avatar(code, title)
        cover = render_cover(code, title, default_tagline(title))
        with open(os.path.join(out_dir, f"{code}_avatar.jpg"), "wb") as fh:
            fh.write(avatar)
        with open(os.path.join(out_dir, f"{code}_cover.jpg"), "wb") as fh:
            fh.write(cover)
        print(f"{code:14s} {title:22s} avatar={len(avatar):6d}b cover={len(cover):6d}b")

    print(f"\nГотово: {len(regions)} регионов -> {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
