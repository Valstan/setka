"""Тесты генерации фирменной графики (modules/promotion/branding.py)."""

import io

import pytest

from modules.promotion.branding import (
    AVATAR_SIZE,
    COVER_H,
    COVER_W,
    TEMPLATE_VERSION,
    default_tagline,
    region_hue,
    render_avatar,
    render_cover,
)

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402


def _img(blob: bytes) -> Image.Image:
    return Image.open(io.BytesIO(blob))


def test_avatar_deterministic():
    """Повторный прогон даёт байт-в-байт тот же файл — иначе сравнение версий слепнет."""
    a = render_avatar("oparino", "Опарино")
    b = render_avatar("oparino", "Опарино")
    assert a == b


def test_cover_deterministic():
    a = render_cover("oparino", "Опарино", "Афиша, новости и объявления")
    b = render_cover("oparino", "Опарино", "Афиша, новости и объявления")
    assert a == b


def test_avatar_size_and_format():
    img = _img(render_avatar("uni", "Уни"))
    assert img.size == (AVATAR_SIZE, AVATAR_SIZE)
    assert img.format == "JPEG"


def test_cover_size_and_format():
    img = _img(render_cover("uni", "Уни", default_tagline("Унинский округ")))
    assert img.size == (COVER_W, COVER_H)
    assert img.format == "JPEG"


def test_longest_name_fits():
    """Самое длинное имя сети не должно ронять рендер и вылезать за холст."""
    blob = render_avatar("belholunitsa", "Белая Холуница")
    assert _img(blob).size == (AVATAR_SIZE, AVATAR_SIZE)
    cover = render_cover("belholunitsa", "Белая Холуница", default_tagline("Белохолуницкий округ"))
    assert _img(cover).size == (COVER_W, COVER_H)


def test_hue_differs_between_regions():
    """Хотя бы на выборке реальной сети цвета в основном различимы."""
    codes = ["oparino", "uni", "kotelnich", "belholunitsa", "murashi", "svecha", "nagorsk"]
    hues = [region_hue(c) for c in codes]
    assert len(set(hues)) == len(hues)


def test_hue_stable():
    """Цвет прибит к коду региона навсегда: смена — это смена бренда."""
    assert region_hue("oparino") == region_hue("oparino")
    assert 0 <= region_hue("anything") < 360


def test_backgrounds_differ():
    """Разные регионы дают визуально разные файлы."""
    assert render_avatar("oparino", "Опарино") != render_avatar("uni", "Уни")


def test_default_tagline_fallback():
    assert "района" in default_tagline("")
    assert "Опаринский" in default_tagline("Опаринский округ")


def test_template_version_is_int():
    assert isinstance(TEMPLATE_VERSION, int) and TEMPLATE_VERSION >= 1
