"""Гейт: браузер не покажет закэшированную СТРАНИЦУ, не спросив сервер.

Парный гейт к ``tests/test_static_revalidation.py``. Тот держит статику, этот —
сам документ, и без второго первый неполон: кэш-бастеры ``?v=…`` записаны
**внутри** HTML, поэтому страница из кэша просит статику по старым адресам, и
``no-cache`` на ``/static/...`` до дела не доходит.

Замер 06.09 на живом проде: правку вкладки планировщика (#657) выкатили, файл
на диске новый, сервисы подняты, health 200 — а обычный переход на
``/ad#scheduler`` отдал старый шаблон (нет ``adShowTabByHash``, тег скрипта с
прежним ``?v=20260905_outreach``). Тот же адрес с уникальным query сразу дал
новую страницу. ``fetch('/ad', {cache: 'no-store'})`` показал ответ без единого
кэш-заголовка.

Проверяется настоящим HTTP-ответом, а не чтением исходника: «в коде написано»
не равно «в ответе приехало» (pool #229 — вердикт выносит только независимое
чтение состояния). Формулировка директивы намеренно не проверяется дословно:
важно не как записано, а что браузер обязан сходить на сервер.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.testclient import TestClient

from middleware.cache_headers import HtmlRevalidationMiddleware


@pytest.fixture(scope="module")
def client() -> TestClient:
    app = FastAPI()
    app.add_middleware(HtmlRevalidationMiddleware)

    @app.get("/page")
    async def page():
        return HTMLResponse("<html><body>страница</body></html>")

    @app.get("/api/data")
    async def data():
        return JSONResponse({"ok": True})

    @app.get("/oidc/keys")
    async def oidc_keys():
        # Роут знает про свой ответ больше общего слоя — его директива старше.
        return JSONResponse({"keys": []}, headers={"Cache-Control": "no-store"})

    @app.get("/page-no-store")
    async def page_no_store():
        return HTMLResponse("<html></html>", headers={"Cache-Control": "no-store"})

    @app.get("/plain")
    async def plain():
        return PlainTextResponse("текст")

    return TestClient(app)


def test_html_forbids_showing_a_copy_without_asking(client: TestClient):
    response = client.get("/page")

    assert response.status_code == 200
    cache_control = response.headers.get("cache-control")
    assert cache_control is not None, (
        "страница приехала без Cache-Control — браузер сам решит, сколько "
        "держать копию, и после деплоя покажет старый шаблон со старой статикой"
    )
    assert "no-cache" in cache_control, (
        f"Cache-Control = {cache_control!r}. Нужна директива, обязывающая "
        "переспросить сервер перед показом страницы."
    )


def test_explicit_directive_of_a_route_wins(client: TestClient):
    """``no-store`` роута строже общего слоя — перетирать его нельзя."""
    assert client.get("/page-no-store").headers["cache-control"] == "no-store"
    assert client.get("/oidc/keys").headers["cache-control"] == "no-store"


def test_non_html_is_not_touched(client: TestClient):
    """Слой лечит документ, а не всё подряд: чужие типы остаются как были."""
    assert "cache-control" not in client.get("/api/data").headers
    assert "cache-control" not in client.get("/plain").headers


def test_wired_into_the_app():
    """Гейт без области не гейт (#284): слой должен стоять в реальном app.

    Проверка в изолированном приложении выше говорит только про сам класс. Если
    его забыли подключить в ``main.py``, все тесты остаются зелёными, а прод —
    сломанным.
    """
    from main import app

    assert any(m.cls is HtmlRevalidationMiddleware for m in app.user_middleware), (
        "HtmlRevalidationMiddleware не подключён в main.py — страницы снова "
        "уходят без Cache-Control"
    )
