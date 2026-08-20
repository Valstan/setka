"""Маршрутизация операторской ленты (``web/api/classifier_review.py``) — HTTP-слой.

**Зачем отдельный файл.** Групповые действия были покрыты тестами на уровне
сервиса (``test_funnel_and_grouping.py``), и все они зелёные — но на проде
кнопка «Согласен со всем блоком» отвечала 422 и не делала ничего. Причина
лежала ровно между тестом и сервисом: статический путь ``/bulk/agree`` был
объявлен ПОСЛЕ параметризованного ``/{classification_id}/agree``, Starlette
матчит маршруты в порядке регистрации, и «bulk» уходил в ``int()``.

Тест сервиса такую поломку увидеть не может в принципе: он зовёт функцию
напрямую, минуя роутер. Поэтому здесь проверяется именно то, что снаружи
называется контрактом — какой URL до какой функции доезжает.

Мини-FastAPI + TestClient; service замокан (без БД).
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from web.api import classifier_review as rev


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(rev.router, prefix="/api/classifier-review")
    return TestClient(app)


def test_bulk_agree_reaches_the_service_and_not_the_id_route(client):
    """POST /bulk/agree обязан доехать до ``service.bulk_agree``.

    Регресс на прод-инцидент 2026-08-19: путь съедался маршрутом
    ``/{classification_id}/agree``, ``int("bulk")`` падал → 422, а фронт
    молча гасил ошибку. Оператор видел «кнопка не работает».
    """
    fake = AsyncMock(return_value={"ok": True, "finalized": 3, "missing": 0})
    with patch.object(rev.service, "bulk_agree", fake):
        r = client.post("/api/classifier-review/bulk/agree", json={"ids": [1, 2, 3]})
    assert r.status_code == 200, r.text
    assert r.json()["finalized"] == 3
    assert fake.await_count == 1
    assert list(fake.await_args.args[1]) == [1, 2, 3]


def test_bulk_correct_reaches_the_service_and_not_the_id_route(client):
    """POST /bulk/correct — та же коллизия с ``/{classification_id}/correct``."""
    fake = AsyncMock(return_value={"ok": True, "corrected": 2})
    with patch.object(rev.service, "bulk_correct", fake):
        r = client.post(
            "/api/classifier-review/bulk/correct",
            json={"ids": [7, 8], "verdict_type": "action", "operator_value": "publish"},
        )
    assert r.status_code == 200, r.text
    assert fake.await_count == 1
    assert fake.await_args.kwargs["verdict_type"] == "action"


def test_single_agree_still_routes_by_id(client):
    """Починка порядка не должна отобрать числовой путь у одиночной кнопки."""
    fake = AsyncMock(return_value={"ok": True, "classification_id": 47481})
    with patch.object(rev.service, "agree_all", fake):
        r = client.post("/api/classifier-review/47481/agree")
    assert r.status_code == 200, r.text
    assert fake.await_args.args[1] == 47481


def _routes_for_method(method: str):
    """Пути + скомпилированные regex роутера для одного HTTP-метода.

    Starlette матчит маршрут по паре (путь, метод): если путь совпал, но
    метод — нет, поиск идёт дальше. Поэтому затенение проверяется ВНУТРИ
    одного метода, а не по всему списку маршрутов сразу.
    """
    return [
        (r.path, r.path_regex) for r in rev.router.routes if method in getattr(r, "methods", set())
    ]


def _shadowed_static_paths(method: str) -> list:
    """Статические пути метода, недостижимые из-за более раннего параметризованного."""
    routes = _routes_for_method(method)
    shadowed = []
    for i, (path, _regex) in enumerate(routes):
        if "{" in path:
            continue  # сам параметризованный — интересуют статические жертвы
        for earlier_path, earlier_regex in routes[:i]:
            if "{" not in earlier_path:
                continue
            if earlier_regex.match(path):
                shadowed.append(f"{path} затенён {earlier_path}")
    return shadowed


def test_no_static_path_is_shadowed_by_a_parameterized_one():
    """Гейт на весь роутер и оба метода, а не на две починенные POST-ручки.

    Проверяется свойство, а не список: если статический путь объявлен после
    параметризованного, который его накрывает, — Starlette отдаст запрос
    первому, и ручка станет недостижимой ровно так же, как ``/bulk/agree``.
    Новый эндпоинт с таким же дефектом обязан ронять этот тест, а не прод.

    GET проверяется наравне с POST (находка ревью #495): старый гейт смотрел
    только на POST и не увидел бы затенение ``GET /rating/top`` вовсе —
    обещанная бри­фом «автоматическая защита» на GET-маршруты не
    распространялась.
    """
    for method in ("GET", "POST"):
        shadowed = _shadowed_static_paths(method)
        assert not shadowed, f"Статические {method}-пути недостижимы: " + "; ".join(shadowed)

    # Сам гейт не должен быть зелёным на пустом входе: если разбор маршрутов
    # однажды перестанет их находить, тест обязан упасть, а не молча пройти.
    gets = _routes_for_method("GET")
    posts = _routes_for_method("POST")
    assert gets, "GET-маршрутов не найдено — тест охраняет пустоту"
    assert posts, "POST-маршрутов не найдено — тест охраняет пустоту"
    # Параметризованные пути сегодня есть только среди POST (/{classification_id}/...) —
    # GET-гейту пока нечего ловить, поэтому это условие проверяем только для POST.
    assert any("{" in p for p, _ in posts), "параметризованных POST нет — гейту нечего ловить"


def test_feed_random_reaches_the_service_and_not_the_id_route(client):
    """GET /feed/random обязан доехать до ``service.review_feed_random``.

    Гейт на затенение (инцидент 2026-08-19): параметризованные
    ``/{classification_id}/…`` объявлены ВЫШЕ этого маршрута, и проверять это
    рассуждением («у них литерал вторым сегментом») мы уже пробовали — стоило
    прод-дня. Проверяем HTTP-запросом.
    """
    fake = AsyncMock(return_value={"blocks": [], "count": 0, "theme": None, "themes_available": 0})
    with patch.object(rev.service, "review_feed_random", fake):
        r = client.get("/api/classifier-review/feed/random?limit=10")
    assert r.status_code == 200, r.text
    assert fake.await_count == 1
    assert fake.await_args.kwargs["limit"] == 10


def test_feed_random_passes_exclude_ids_as_numbers(client):
    """``exclude`` приходит строкой CSV, сервис ждёт числа. Мусор отбрасывается,
    а не роняет ручку 422: список показанного — подсказка, а не контракт."""
    fake = AsyncMock(return_value={"blocks": [], "count": 0})
    with patch.object(rev.service, "review_feed_random", fake):
        r = client.get("/api/classifier-review/feed/random?exclude=5,7;9,%20,abc")
    assert r.status_code == 200, r.text
    assert fake.await_args.kwargs["exclude_ids"] == [5, 7, 9]
