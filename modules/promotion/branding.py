"""Генерация фирменной графики ИНФО-сообществ: аватар и обложка.

Ребрендинг сети (план 2026-08-29): у 25 сообществ раскатки нет ни аватара, ни
обложки, а ВК официально называет оформление положительным фактором ленты и
обучает поиск на конверсии «показ в выдаче → вступление»
(``docs/ops/vk-findability-playbook.md``). Рисуем плакатный стиль по заказу
владельца: аватар — цветной, диагональные полосы, две строки «ОПАРИНО / ИНФО»;
обложка — крупно «ОПАРИНО - ИНФО», ниже тэглайн одной строкой.

Три инженерных решения:

- **Детерминизм.** Никакого random — только sha256 от кода региона. Повторный
  прогон даёт байт-в-байт тот же файл, поэтому «изменилась ли графика» решается
  сравнением байтов, а версия шаблона живёт в ``TEMPLATE_VERSION`` (бамп при
  правке дизайна = переприменение через ``promo_group_setup.setup_version``).
- **Свой шрифт в репо** (``assets/fonts/DejaVuSans-Bold.ttf``, свободная
  лицензия Bitstream Vera). Системные шрифты не годятся: на Windows-деве DejaVu
  нет вовсе, на Linux путь зависит от пакета — а рендер обязан быть одинаковым.
- **Палитра из хэша.** Оттенок = ``sha256(code) % 360`` при фиксированных
  насыщенности и светлоте: 40+ сообществ различимы между собой, но выглядят
  одной сетью. Смещать оттенок руками не надо — цвет «прибит» к коду региона
  навсегда, и сосед в футерах/репостах всегда узнаваем.

Размеры — по замерам пробы (``docs/ops/group-setup-probe.md``) и канону ВК:
аватар 1024×1024 (минимум 200, важное — в центре, ВК кропает в круг),
обложка 2560×644 с безопасной зоной 1590×400 по центру (мобильный клиент
обрезает края; канон CROP зафиксирован пробой обложек 2026-06-14).
"""

from __future__ import annotations

import colorsys
import hashlib
import io
import os
from typing import List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

# Версия шаблона: бамп при любой правке дизайна ниже. Канал setup пишет её в
# promo_group_setup.setup_version — повтор той же версии = no-op по уникуму.
TEMPLATE_VERSION = 1

AVATAR_SIZE = 1024
COVER_W, COVER_H = 2560, 644
# Безопасная зона обложки: канон CROP 1590x400, отмасштабированный к 2560 —
# всё важное держим внутри центральных ~62% ширины и ~62% высоты.
COVER_SAFE_W, COVER_SAFE_H = 1590, 400

_FONT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "assets",
    "fonts",
    "DejaVuSans-Bold.ttf",
)

# Палитра сети: насыщенность/светлота фиксированы, оттенок — из хэша кода.
_SAT, _LIGHT = 0.58, 0.40
_STRIPE_LIGHT = 0.30  # полосы темнее фона — читаются, но не спорят с текстом
_JPEG_QUALITY = 92


def region_hue(code: str) -> int:
    """Оттенок региона: детерминированный, 0..359."""
    digest = hashlib.sha256(code.strip().lower().encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") % 360


def _hls_rgb(hue: int, light: float, sat: float) -> Tuple[int, int, int]:
    r, g, b = colorsys.hls_to_rgb((hue % 360) / 360.0, light, sat)
    return int(r * 255), int(g * 255), int(b * 255)


def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(_FONT_PATH, size)


def _fit_font(
    draw: ImageDraw.ImageDraw, text: str, max_w: int, max_size: int
) -> ImageFont.FreeTypeFont:
    """Максимальный кегль, при котором строка влезает в ``max_w`` (бинарный поиск)."""
    lo, hi = 8, max_size
    while lo < hi:
        mid = (lo + hi + 1) // 2
        box = draw.textbbox((0, 0), text, font=_font(mid))
        if box[2] - box[0] <= max_w:
            lo = mid
        else:
            hi = mid - 1
    return _font(lo)


def _text_w(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> int:
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0]


def _diagonal_stripes(
    img: Image.Image, hue: int, *, count: int, width: int, spacing: int, offset: int
) -> None:
    """Диагональные полосы (45°) тёмным тоном того же оттенка."""
    draw = ImageDraw.Draw(img)
    color = _hls_rgb(hue, _STRIPE_LIGHT, _SAT)
    w, h = img.size
    # Стартуем левее края, чтобы диагонали покрыли весь холст.
    x = -h + offset
    for _ in range(count):
        draw.line([(x, h), (x + h, 0)], fill=color, width=width)
        x += spacing


def _title_lines(name: str) -> List[str]:
    """Аватар: слова имени построчно + строка «ИНФО» всегда последней.

    «Опарино» → [«ОПАРИНО», «ИНФО»]; «Белая Холуница» → [«БЕЛАЯ», «ХОЛУНИЦА»,
    «ИНФО»] — иначе у двухсловных имён «ИНФО» терялась.
    """
    return name.upper().split() + ["ИНФО"]


def render_avatar(code: str, name: str) -> bytes:
    """Аватар 1024×1024 JPEG: фон-оттенок, полосы, «ИМЯ / ИНФО» по центру.

    ``name`` — короткое имя района без суффиксов («Опарино», «Белая Холуница»);
    в две строки: имя (при двух словах — само по себе двумя строками) + «ИНФО».
    Всё важное — в центральном круге: ВК показывает аватар кругом.
    """
    hue = region_hue(code)
    img = Image.new("RGB", (AVATAR_SIZE, AVATAR_SIZE), _hls_rgb(hue, _LIGHT, _SAT))
    _diagonal_stripes(img, hue, count=5, width=56, spacing=300, offset=hue % 300)
    draw = ImageDraw.Draw(img)

    lines: List[str] = _title_lines(name)

    # Центральный круг ~72% стороны — гарантия видимости после круглого кропа.
    max_text_w = int(AVATAR_SIZE * 0.66)
    fonts = [_fit_font(draw, line, max_text_w, 200) for line in lines]
    heights = [draw.textbbox((0, 0), t, font=f)[3] for t, f in zip(lines, fonts)]
    gap = 28
    total_h = sum(heights) + gap * (len(lines) - 1)
    y = (AVATAR_SIZE - total_h) // 2

    for text, font, h in zip(lines, fonts, heights):
        w = _text_w(draw, text, font)
        x = (AVATAR_SIZE - w) // 2
        draw.text((x + 4, y + 4), text, font=font, fill=_hls_rgb(hue, 0.16, _SAT))  # тень
        draw.text((x, y), text, font=font, fill=(255, 255, 255))
        y += h + gap

    out = io.BytesIO()
    img.save(out, "JPEG", quality=_JPEG_QUALITY)
    return out.getvalue()


def render_cover(code: str, name: str, tagline: str) -> bytes:
    """Обложка 2560×644 JPEG: «ИМЯ - ИНФО» крупно + тэглайн строкой ниже.

    Текст держится в безопасной зоне 1590×400 по центру: мобильный клиент
    обрезает края обложки, десктоп показывает целиком.
    """
    hue = region_hue(code)
    img = Image.new("RGB", (COVER_W, COVER_H), _hls_rgb(hue, _LIGHT, _SAT))
    _diagonal_stripes(img, hue, count=8, width=64, spacing=420, offset=hue % 420)
    draw = ImageDraw.Draw(img)

    title = f"{name.upper()} - ИНФО"
    safe_w = COVER_SAFE_W - 120  # поля внутри безопасной зоны
    title_font = _fit_font(draw, title, safe_w, 190)
    tag_font = _fit_font(draw, tagline, safe_w, 64)

    t_box = draw.textbbox((0, 0), title, font=title_font)
    g_box = draw.textbbox((0, 0), tagline, font=tag_font)
    t_h, g_h = t_box[3] - t_box[1], g_box[3] - g_box[1]
    gap = 30
    total_h = t_h + gap + g_h
    y = (COVER_H - total_h) // 2 - t_box[1]

    for text, font, h_top in ((title, title_font, t_box[1]), (tagline, tag_font, g_box[1])):
        w = _text_w(draw, text, font)
        x = (COVER_W - w) // 2
        draw.text((x + 5, y + 5), text, font=font, fill=_hls_rgb(hue, 0.16, _SAT))
        draw.text((x, y), text, font=font, fill=(255, 255, 255))
        box = draw.textbbox((0, 0), text, font=font)
        y += (box[3] - box[1]) + gap

    out = io.BytesIO()
    img.save(out, "JPEG", quality=_JPEG_QUALITY)
    return out.getvalue()


def default_tagline(district_name: str, center_city: Optional[str] = None) -> str:
    """Тэглайн обложки: «Афиша, новости и объявления Опаринского округа»."""
    base = (district_name or "").strip() or (center_city or "").strip()
    if base:
        return f"Афиша, новости и объявления — {base}"
    return "Афиша, новости и объявления района"
