#!/usr/bin/env python3
"""Генерация PNG-иконок PWA Радара из геометрии web/static/radar/icon.svg.

Зачем не конвертер SVG→PNG: cairosvg требует нативный cairo (боль на
Windows), а иконка простая — рисуем ту же геометрию Pillow'ом в supersample
×4 и даунскейлим. Запускать при изменении дизайна иконки:

    ./venv/Scripts/python.exe scripts/generate_radar_icons.py

Pillow — dev-only зависимость (в requirements.txt не входит): PNG
коммитятся готовыми, на проде ничего не генерится.
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

OUT_DIR = Path(__file__).resolve().parent.parent / "web" / "static" / "radar"
BLUE = (13, 110, 253, 255)  # #0d6efd (bootstrap primary, как в SVG)
WHITE = (255, 255, 255, 255)

# Геометрия из icon.svg (canvas 512×512): скругление 96, центр радара
# (256, 296), дуги радиусов 99/170/241 в секторе 225°-315°, точка r=22,
# «стрелка» в (368, 184).
S = 4  # supersample


def _draw(size: int) -> Image.Image:
    c = 512 * S
    img = Image.new("RGBA", (c, c), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    d.rounded_rectangle([0, 0, c - 1, c - 1], radius=96 * S, fill=BLUE)

    cx, cy = 256 * S, 296 * S
    stroke = 28 * S
    for r in (99, 170, 241):
        rs = r * S
        d.arc([cx - rs, cy - rs, cx + rs, cy + rs], start=225, end=315, fill=WHITE, width=stroke)
        # Скруглённые торцы дуг (stroke-linecap=round в SVG):
        for ang in (225, 315):
            x = cx + rs * math.cos(math.radians(ang))
            y = cy + rs * math.sin(math.radians(ang))
            hw = stroke / 2
            d.ellipse([x - hw, y - hw, x + hw, y + hw], fill=WHITE)

    # Стрелка к (368, 184) + центральная точка.
    nx, ny = 368 * S, 184 * S
    d.line([cx, cy, nx, ny], fill=WHITE, width=20 * S)
    for x, y, r in ((nx, ny, 10), (cx, cy, 22)):
        rs = r * S
        d.ellipse([x - rs, y - rs, x + rs, y + rs], fill=WHITE)

    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    for size in (192, 512):
        path = OUT_DIR / f"icon-{size}.png"
        _draw(size).save(path, "PNG", optimize=True)
        print(f"written {path} ({path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
