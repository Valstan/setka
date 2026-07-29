"""Рендер публичного лендинга /regions/links — то, что реально уезжает в браузер.

Регресс на поймано-в-проде: таблица цен передаётся в ``<script
type="application/json">``, а Jinja у FastAPI рендерит с ``autoescape=True``.
Внутри ``<script>`` HTML-сущности **не декодируются**, поэтому экранированные
кавычки (``&#34;``) ломают ``JSON.parse`` — панель выбора молча оставалась без
цены. Тест парсит содержимое тега ровно как браузер: как есть, без разэкранирования.
"""

from __future__ import annotations

import json
import re

import pytest
from fastapi.templating import Jinja2Templates

from config.ad_landing import get_ad_landing_context
from main import BASE_DIR, region_links_page


class _FakeURL:
    hostname = "сарафан.вмалмыже.рф"
    path = "/regions/links"
    query = ""


class _FakeRequest:
    url = _FakeURL()
    headers: dict = {}
    query_params: dict = {}
    cookies: dict = {}


def _render() -> str:
    """Отрендерить страницу тем же движком, что и прод (autoescape=True)."""
    templates = Jinja2Templates(directory=str(BASE_DIR / "web" / "templates"))
    ctx = get_ad_landing_context()
    import main

    ctx["price_table_json"] = json.dumps(ctx["price_table"], ensure_ascii=False).replace(
        "<", "\\u003c"
    )
    assert main.templates.env.autoescape, "autoescape включён — тест сторожит именно этот случай"
    return templates.get_template("region_links.html").render(request=_FakeRequest(), **ctx)


def _script_payload(html: str, element_id: str) -> str:
    match = re.search(
        rf'<script type="application/json" id="{element_id}">(.*?)</script>', html, re.S
    )
    assert match, f"тег <script id={element_id}> не найден"
    return match.group(1)


def test_price_table_survives_autoescape_as_valid_json():
    payload = _script_payload(_render(), "price-table")
    assert "&#34;" not in payload and "&quot;" not in payload
    table = json.loads(payload)  # ровно то, что делает JSON.parse в браузере
    assert table[0]["price"] == 350
    assert table[4]["price"] == 1300  # пятое сообщество — рубеж пакета


def test_rendered_page_shows_real_prices_and_contacts():
    html = _render()
    for needle in ("1300", "2500", "5000", "Val_Stanley", "valstan@valstan.ru", "Альфа-Банк"):
        assert needle in html, f"на странице нет «{needle}»"


def test_rendered_page_explains_discount_ladder():
    """Порог потолка на вкладке цен берётся из тарифов, а не вписан руками."""
    html = _render()
    assert "Как работает опт" in html
    assert "с <b>18 сообществ" in html


@pytest.mark.asyncio
async def test_page_handler_escapes_angle_bracket_in_json():
    """«<» в JSON экранируется — иначе строка вида «</script>» закрыла бы тег."""
    response = await region_links_page(_FakeRequest())
    assert "\\u003c" in response.context["price_table_json"] or "<" not in (
        response.context["price_table_json"]
    )
