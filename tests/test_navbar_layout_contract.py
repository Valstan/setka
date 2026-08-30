"""Гейт: место под закреплённый навбар меряется, а не угадывается константой.

31.08, разбирая жалобу «сайт весь поехал», нашли живой баг вёрстки. Навбар был
вынут из потока **только стилем** (`.navbar { position: fixed }` в style.css), а
место под него держала магическая константа `main { margin-top: 76px }` при
фактической высоте навбара ~62px.

Отказ давал рассинхрон двух чисел, которые никто не сверял:

* `navbar-expand-lg` схлопывает меню в гамбургер ниже **992px**;
* компенсирующий `margin-top: 70px` включался ниже **768px**.

В полосе между ними раскрытый гамбургер вырастал до 454px, отступ оставался
76px, и фиксированная шапка накрывала **378px** контента (замер на проде
2026-08-31, ширина окна 884px). Никакого деплоя для этого не требовалось —
достаточно сузить окно.

Почему гейт, а не просто починка: константа тут выглядит безобидно и
восстанавливается одной строкой «подберу отступ на глаз». Но высота навбара —
величина **производная** (ширина окна, подставленный шрифт, состояние
гамбургера, число пунктов меню), и любое её угаданное значение верно ровно до
первой правки меню. Поэтому проверяется не «отступ равен правильному числу», а
что число вообще не зашито: раскладка живёт в разметке классом `fixed-top`,
высота приезжает измеренной через `--navbar-h`.

Сверяется только навбар `base.html`. У `radar.html` и `advertiser_cabinet.html`
навбары свои, на `sticky-top`, и `style.css` они не грузят — они остаются в
потоке, и резервировать под них место не нужно.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BASE_HTML = REPO_ROOT / "web" / "templates" / "base.html"
STYLE_CSS = REPO_ROOT / "web" / "static" / "css" / "style.css"


def _base_html() -> str:
    return BASE_HTML.read_text(encoding="utf-8")


def _style_css() -> str:
    return STYLE_CSS.read_text(encoding="utf-8")


def test_navbar_is_taken_out_of_flow_by_markup_not_by_stylesheet() -> None:
    """`fixed-top` — в разметке; иначе правило бьёт по любому чужому навбару."""
    nav_tags = re.findall(r"<nav\b[^>]*>", _base_html())
    assert nav_tags, "в base.html не нашлось ни одного <nav>"
    assert any("fixed-top" in tag for tag in nav_tags), (
        "навбар base.html потерял класс fixed-top — раскладка снова уехала в CSS:\n  "
        + "\n  ".join(nav_tags)
    )


def _strip_css_comments(css: str) -> str:
    """Комментарии — не объявления.

    Первая ревизия этого теста падала на собственном комментарии внутри
    правила: там словами перечислено, какие свойства отсюда УБРАНЫ
    («position/top/z-index задаёт fixed-top»). Гейт обязан читать объявления,
    иначе он запрещает объяснять решение рядом с кодом.
    """
    return re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)


def test_navbar_rule_does_not_position_itself_from_css() -> None:
    block = re.search(r"^\.navbar\s*\{([^}]*)\}", _strip_css_comments(_style_css()), re.MULTILINE)
    assert block, "в style.css не нашлось правила .navbar"

    body = block.group(1)
    forbidden = [prop for prop in ("position", "z-index", "top:") if prop in body]
    assert not forbidden, (
        f"правило .navbar снова задаёт раскладку из CSS ({forbidden}). "
        "Это глобальный селектор: он попадёт по любому второму навбару на "
        "странице. Раскладка — классом fixed-top в разметке."
    )


def test_space_under_navbar_is_measured_not_hardcoded() -> None:
    css = _strip_css_comments(_style_css())

    main_rules = re.findall(r"(?:^|\s)main\s*\{([^}]*)\}", css, re.MULTILINE)
    assert main_rules, "в style.css не нашлось ни одного правила для main"

    hardcoded = []
    uses_var = False
    for body in main_rules:
        for value in re.findall(r"margin-top\s*:\s*([^;]+);", body):
            if "--navbar-h" in value:
                uses_var = True
            elif re.search(r"\d", value):
                hardcoded.append(value.strip())

    assert uses_var, (
        "main больше не берёт отступ из --navbar-h — высота навбара снова "
        "угадывается вместо измерения."
    )
    assert not hardcoded, (
        f"под навбар снова зашита константа: {hardcoded}. Высота навбара "
        "производна от ширины окна, шрифта и состояния гамбургера — любое "
        "угаданное число верно до первой правки меню."
    )


def test_measuring_script_is_wired_to_everything_that_changes_the_height() -> None:
    """Мерить один раз на загрузке мало — высота меняется уже после неё."""
    html = _base_html()

    assert "--navbar-h" in html, "скрипт не выставляет --navbar-h"
    assert "offsetHeight" in html, "высота навбара не измеряется, а откуда-то берётся"

    for event, why in (
        ("resize", "смена ширины окна меняет высоту навбара"),
        ("shown.bs.collapse", "раскрытие гамбургера вырастает в сотни пикселей"),
        ("hidden.bs.collapse", "после схлопывания отступ обязан вернуться"),
    ):
        assert event in html, f"скрипт не слушает {event}: {why}"


def test_measuring_script_runs_after_bootstrap_bundle() -> None:
    """Слушать `*.bs.collapse` до загрузки бандла бессмысленно."""
    html = _base_html()
    bundle = html.find("bootstrap.bundle.min.js")
    wiring = html.find("shown.bs.collapse")

    assert bundle != -1, "в base.html не нашёлся бандл Bootstrap"
    assert wiring != -1, "в base.html не нашлась подписка на shown.bs.collapse"
    assert bundle < wiring, (
        "скрипт измерения стоит ВЫШЕ бандла Bootstrap — на момент подписки "
        "событий collapse ещё не существует, и раскрытый гамбургер снова "
        "накроет контент."
    )
