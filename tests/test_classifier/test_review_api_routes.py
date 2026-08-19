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


def test_no_static_post_path_is_shadowed_by_a_parameterized_one():
    """Гейт на весь роутер, а не на две починенные ручки.

    Проверяется свойство, а не список: если статический POST-путь объявлен
    после параметризованного, который его накрывает, — Starlette отдаст запрос
    первому, и ручка станет недостижимой ровно так же, как ``/bulk/agree``.
    Новый эндпоинт с таким же дефектом обязан ронять этот тест, а не прод.
    """
    posts = [
        (r.path, r.path_regex) for r in rev.router.routes if "POST" in getattr(r, "methods", set())
    ]
    shadowed = []
    for i, (path, _regex) in enumerate(posts):
        if "{" in path:
            continue  # сам параметризованный — интересуют статические жертвы
        for earlier_path, earlier_regex in posts[:i]:
            if "{" not in earlier_path:
                continue
            if earlier_regex.match(path):
                shadowed.append(f"{path} затенён {earlier_path}")
    assert not shadowed, "Статические пути недостижимы: " + "; ".join(shadowed)

    # Сам гейт не должен быть зелёным на пустом входе: если разбор маршрутов
    # однажды перестанет их находить, тест обязан упасть, а не молча пройти.
    assert posts, "POST-маршрутов не найдено — тест охраняет пустоту"
    assert any("{" in p for p, _ in posts), "параметризованных POST нет — гейту нечего ловить"
