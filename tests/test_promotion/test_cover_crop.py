"""Гейт: ВК получает кроп, равный ФАКТИЧЕСКОМУ размеру загружаемой обложки.

Инцидент 2026-08-31 (жалоба владельца: «обложки не вмещаются, обрезаются»).
Десять сообществ порции 1, оформленных 30.08, показывали обрезанный заголовок:
вместо «КИКНУР - ИНФО» — «КИКНУР - И».

Причина — два числа, которые обязаны совпадать, но лежали в разных файлах:

* ``branding.render_cover`` рисует холст **2560×644**;
* ``group_setup_vk.COVER_CROP`` просил у ВК прямоугольник **1590×400**.

``crop_x2/crop_y2`` у ``photos.getOwnerCoverPhotoUploadServer`` задаются в
координатах ЗАГРУЖАЕМОЙ картинки: ВК берёт указанный прямоугольник, остальное
отрезает. 1590 из 2560 — это 62% ширины, и текст, лежащий по центру в полосе
547..2013, обрывался ровно на 71% своей длины. Совпадение расчёта с тем, что
владелец увидел на экране, и было доказательством причины.

**Почему отказ дожил до жалобы.** Со стороны кода всё было успешно: API вернул
ok, обложка встала, в логах ни одной ошибки. Увидеть обрезку можно было только
глазами на живой странице. Это ровно pool #229 — успех write-API не означает,
что применилось задуманное; вердикт выносит независимое чтение состояния.

**Почему гейт сверяет с рендером, а не с константой.** Сверять ``COVER_CROP`` с
``COVER_W/COVER_H`` бессмысленно: теперь они берутся из одного места и совпадут
по построению. Ошибка была не в арифметике, а в том, что кроп описывал ДРУГУЮ
картинку. Поэтому здесь обложка честно рендерится, её размер читается Pillow —
и с ним сверяется кроп. Тогда любое расхождение между тем, что рисуется, и тем,
что заявляется ВК, ловится независимо от того, как записаны константы.
"""

from __future__ import annotations

import io

from PIL import Image

from modules.promotion.branding import COVER_H, COVER_W, render_cover
from modules.promotion.group_setup_vk import COVER_CROP


def _rendered_size() -> tuple[int, int]:
    data = render_cover("kiknur", "Кикнур", "Афиша, новости и объявления — Кикнур")
    return Image.open(io.BytesIO(data)).size


def test_crop_covers_the_whole_rendered_image() -> None:
    width, height = _rendered_size()

    assert COVER_CROP["crop_x"] == 0 and COVER_CROP["crop_y"] == 0, (
        f"кроп начинается не с левого верхнего угла: {COVER_CROP} — часть обложки "
        "будет отрезана сверху или слева."
    )
    assert COVER_CROP["crop_x2"] == width, (
        f"ВК попросят вырезать {COVER_CROP['crop_x2']}px из картинки шириной {width}px — "
        f"отрежется {100.0 * (width - COVER_CROP['crop_x2']) / width:.0f}% ширины, "
        "и текст обложки оборвётся на середине слова."
    )
    assert COVER_CROP["crop_y2"] == height, (
        f"ВК попросят вырезать {COVER_CROP['crop_y2']}px из картинки высотой {height}px — "
        f"отрежется {100.0 * (height - COVER_CROP['crop_y2']) / height:.0f}% высоты."
    )


def test_declared_canvas_matches_what_is_actually_drawn() -> None:
    """Константы холста не разошлись с рендером — на них опирается кроп."""
    assert _rendered_size() == (COVER_W, COVER_H)


# Пропорция, в которой ВК ХРАНИТ обложку сообщества. Не «рекомендованный размер»
# из статьи, а живой ответ `groups.getById(fields="cover")` от 2026-08-31:
# 200×80, 400×160, 911×364, 1080×432, 1920×768 — все 2.5:1, ни одного другого.
VK_COVER_RATIO = 2.5


def test_canvas_ratio_matches_the_one_vk_stores() -> None:
    """Несовпадение пропорции режет бока при ЛЮБЫХ crop-параметрах.

    Первая попытка починки 31.08 исправила кроп (1590×400 → весь холст) и не
    помогла: ВК всё равно ужимал картинку 3.975:1 в свои 2.5:1 и отрезал бока.
    Кроп управляет тем, ЧТО взять из картинки, а не тем, в какой пропорции это
    хранить, — поэтому пропорция холста и есть настоящий контракт с ВК.

    Проверять руками: `groups.getById(group_id=<id>, fields="cover")` печатает
    все варианты с размерами. Разъехалось — менять COVER_W/COVER_H, а не тест.
    """
    width, height = _rendered_size()
    ratio = width / height

    assert abs(ratio - VK_COVER_RATIO) < 0.01, (
        f"холст {width}×{height} даёт пропорцию {ratio:.3f}, а ВК хранит обложку "
        f"в {VK_COVER_RATIO}. Разница означает, что ВК сам ужмёт картинку в свою "
        "пропорцию и отрежет бока — заголовок оборвётся на середине слова, "
        "и никакие crop-параметры загрузки это не исправят."
    )


def test_safe_zone_lies_inside_the_canvas() -> None:
    """Безопасная зона шире холста — бессмысленная защита."""
    from modules.promotion.branding import COVER_SAFE_H, COVER_SAFE_W

    assert COVER_SAFE_W <= COVER_W, f"безопасная зона {COVER_SAFE_W} шире холста {COVER_W}"
    assert COVER_SAFE_H <= COVER_H, f"безопасная зона {COVER_SAFE_H} выше холста {COVER_H}"


def test_title_survives_the_crop_that_vk_will_apply() -> None:
    """Прямая проверка симптома: заголовок целиком внутри вырезаемой области.

    Считается не по константам, а по фактической геометрии: где нарисован текст
    и что из этого попадёт в кроп. Именно этот расчёт совпал с тем, что владелец
    увидел на экране 31.08.
    """
    from PIL import ImageDraw

    from modules.promotion import branding as b

    draw = ImageDraw.Draw(Image.new("RGB", (b.COVER_W, b.COVER_H)))
    title = "КИКНУР - ИНФО"
    font = b._fit_font(draw, title, b.COVER_SAFE_W - 120, 190)
    text_w = b._text_w(draw, title, font)

    left = (b.COVER_W - text_w) // 2
    right = left + text_w

    assert left >= COVER_CROP["crop_x"], f"заголовок начинается левее кропа: {left}"
    assert right <= COVER_CROP["crop_x2"], (
        f"заголовок кончается на {right}px, а кроп обрывается на "
        f"{COVER_CROP['crop_x2']}px — видно будет "
        f"{100.0 * (COVER_CROP['crop_x2'] - left) / text_w:.0f}% заголовка."
    )
