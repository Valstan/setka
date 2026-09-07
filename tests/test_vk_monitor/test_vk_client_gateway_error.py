"""``VKClient.api_call`` различает отказ ШЛЮЗА ВК и ответ API.

Корень аварии 07.09 сидел ровно здесь. ``vk_api.exceptions.ApiHttpError``
наследует ``VkApiError``, а **не** ``ApiError`` — значит ветка
``except ApiError`` его не ловила, и HTTP 5xx проваливался в generic-ветку
``except Exception``, где остаётся только ``str(e)``. Наружу уходило
``{"error": {"error_msg": "Response code 502"}}``: ни кода, ни признака класса.

Дальше по цепочке всё сравнивает код — fallback на community-токен по 15/27,
ротация publish-кандидата по 5/17/29. Отсутствующий код превращался в ноль, ноль
не совпадал ни с чем, и отказ шёл прямиком в лицо оператору.

Здесь проверяется самое нижнее звено: класс ошибки не теряется.
"""

from unittest.mock import MagicMock, patch

import pytest
from vk_api.exceptions import ApiError, ApiHttpError

from modules.vk_monitor.vk_client import VKClient


@pytest.fixture
def client():
    with patch("modules.vk_monitor.vk_client.vk_api.VkApi") as m:
        session = MagicMock()
        m.return_value = session
        m.return_value.get_api.return_value = MagicMock()
        c = VKClient("token-gateway-test")
    c._enforce_rate_limit = lambda method: None
    return c


def _http_error(status: int) -> ApiHttpError:
    """ApiHttpError с ответом заданного статуса — как его строит vk_api."""
    response = MagicMock()
    response.status_code = status
    return ApiHttpError(MagicMock(), "wall.post", {}, False, response)


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_gateway_error_is_marked_transient(client, status):
    client.session.method.side_effect = _http_error(status)

    got = client.api_call("wall.post", {"owner_id": -1})

    err = got["error"]
    assert err["transient"] is True
    assert err["http_status"] == status
    assert "error_code" not in err, (
        "кода у HTTP-отказа нет; синтетический утёк бы в vk_error_code отчётов "
        "и читался бы как настоящий ответ ВК"
    )


def test_ordinary_api_error_keeps_its_code_and_is_not_transient(client):
    """Гвоздь: обычный ответ ВК не должен переехать в новый класс."""
    err = ApiError(MagicMock(), "wall.post", {}, False, {"error_code": 15, "error_msg": "denied"})
    client.session.method.side_effect = err

    got = client.api_call("wall.post", {"owner_id": -1})

    assert got["error"]["error_code"] == 15
    assert got["error"].get("transient") is not True


def test_unknown_failure_stays_in_the_generic_branch(client):
    """Сетевой сбой не выдаёт себя за отказ ВК: transient ему не приписываем."""
    client.session.method.side_effect = OSError("connection reset")

    got = client.api_call("wall.post", {"owner_id": -1})

    assert got["error"].get("transient") is not True
    assert "connection reset" in got["error"]["error_msg"]


def test_success_path_untouched(client):
    client.session.method.return_value = {"response": {"post_id": 7}}
    assert client.api_call("wall.post", {"owner_id": -1}) == {"response": {"post_id": 7}}
