"""Гейт: браузер не покажет закэшированную статику, не спросив сервер.

31.08 правка вёрстки выкатилась на прод, прод её принял — а браузер продолжил
рисовать старую страницу. Замер на живом проде показал несогласованную пару:
сервер отдавал новый ``style.css`` (с ``--navbar-h``), браузер применял старый
(с ``margin-top: 76px``), а в заголовках ответа не было ``Cache-Control`` —
только ``ETag`` и ``Last-Modified``.

Без ``Cache-Control`` срок жизни копии выбирает браузер, и он вправе взять её
из кэша **не обращаясь к серверу**. Тогда после деплоя посетитель получает
новый HTML и старый CSS одновременно. Это не «правка не доехала» — это пара
файлов из разных версий, и выглядит она как разъехавшаяся вёрстка.

Гейт держит ровно то свойство, которое чинит рассинхрон: **копию нельзя
показать без переспроса**. Он намеренно не проверяет ``max-age``, ``must-
revalidate`` и прочие формулировки — важно не как записано, а что браузер
обязан сходить на сервер.

Проверяется настоящим HTTP-ответом, а не чтением исходника: заголовок ставится
внутри Starlette, и «в коде написано» не равно «в ответе приехало» (pool #229 —
вердикт выносит только независимое чтение состояния).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from web.static_files import RevalidatingStaticFiles

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = REPO_ROOT / "web" / "static"

# Файлы, чья рассинхронизация с HTML и даёт «вёрстка поехала».
PROBE_PATHS = [
    "/static/css/style.css",
    "/static/vendor/bootstrap/bootstrap.min.css",
    "/static/vendor/bootstrap-icons/bootstrap-icons.css",
    "/static/js/main.js",
]


@pytest.fixture(scope="module")
def client() -> TestClient:
    app = FastAPI()
    app.mount("/static", RevalidatingStaticFiles(directory=str(STATIC_DIR)), name="static")
    return TestClient(app)


@pytest.mark.parametrize("path", PROBE_PATHS)
def test_static_response_forbids_showing_a_copy_without_asking(client: TestClient, path: str):
    response = client.get(path)

    assert response.status_code == 200, f"{path} не отдался"
    cache_control = response.headers.get("cache-control")
    assert cache_control is not None, (
        f"{path} приехал без Cache-Control — браузер сам решит, сколько держать "
        "копию, и после деплоя покажет старый файл рядом с новым HTML."
    )
    assert "no-cache" in cache_control, (
        f"{path}: Cache-Control = {cache_control!r}. Нужна директива, обязывающая "
        "переспросить сервер перед показом."
    )


def test_revalidation_directive_survives_the_304(client: TestClient):
    """На перепроверке директива обязана приехать снова.

    Иначе браузер обновит запись в кэше ответом без ``Cache-Control`` и в
    следующий раз опять решит срок жизни сам — отказ вернётся через один цикл.
    """
    first = client.get("/static/css/style.css")
    etag = first.headers.get("etag")
    assert etag, "статика отдаётся без ETag — перепроверка станет полной загрузкой"

    second = client.get("/static/css/style.css", headers={"If-None-Match": etag})

    assert second.status_code == 304, f"ожидался 304 на известный ETag, пришёл {second.status_code}"
    assert "no-cache" in (second.headers.get("cache-control") or ""), (
        "304 приехал без Cache-Control — директива теряется на первой же "
        "перепроверке, и кэш снова становится бесконтрольным."
    )
