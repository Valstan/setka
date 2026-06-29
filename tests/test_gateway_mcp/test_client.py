"""Тесты ядра MCP-обёртки VK-шлюза (gateway_mcp/client.py).

Только httpx, без пакета ``mcp`` и без сети: проверяем разбор ответа, подсказки
по кодам, конфиг клиента и построение запроса (httpx.AsyncClient замокан).
``server.py`` тут НЕ импортируется (требует mcp, которого нет в venv SARAFAN).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway_mcp.client import (
    DEFAULT_GATEWAY_URL,
    GatewayClient,
    GatewayError,
    hint_for_status,
    parse_response,
)


# ------------------------------------------------------------- parse_response
def test_parse_response_ok_passthrough():
    body = {"ok": True, "response": {"items": [1, 2]}}
    assert parse_response(200, body) == body


def test_parse_response_domain_error_is_data_not_raise():
    # VK-доменная ошибка приходит в теле 200 — это данные, не исключение.
    body = {"ok": False, "error": {"error_code": 15, "error_msg": "Access denied"}}
    assert parse_response(200, body) == body


def test_parse_response_non_dict_body_raises():
    with pytest.raises(GatewayError):
        parse_response(200, "не json-объект")


@pytest.mark.parametrize("status", [400, 401, 429, 503, 502])
def test_parse_response_non_200_raises(status):
    with pytest.raises(GatewayError):
        parse_response(status, None)


# --------------------------------------------------------------- hint_for_status
def test_hint_401_points_to_key():
    assert "SARAFAN_GATEWAY_KEY" in hint_for_status(401)


def test_hint_429_includes_retry_after():
    assert "42" in hint_for_status(429, retry_after="42")


def test_hint_400_mentions_allowlist():
    assert "allowlist" in hint_for_status(400).lower()


def test_hint_503_mentions_unavailable():
    assert "503" in hint_for_status(503)


# --------------------------------------------------------------- GatewayClient
def test_base_url_default(monkeypatch):
    monkeypatch.delenv("SARAFAN_GATEWAY_URL", raising=False)
    c = GatewayClient(api_key="k")
    assert c.base_url == DEFAULT_GATEWAY_URL


def test_base_url_env_and_trailing_slash_stripped(monkeypatch):
    monkeypatch.setenv("SARAFAN_GATEWAY_URL", "https://example.test/")
    c = GatewayClient(api_key="k")
    assert c.base_url == "https://example.test"


def test_api_key_from_env(monkeypatch):
    monkeypatch.setenv("SARAFAN_GATEWAY_KEY", "from-env")
    c = GatewayClient()
    assert c.api_key == "from-env"


def test_headers_without_key_raises(monkeypatch):
    monkeypatch.delenv("SARAFAN_GATEWAY_KEY", raising=False)
    c = GatewayClient(api_key="")
    with pytest.raises(GatewayError):
        c._headers()


def test_headers_with_key():
    c = GatewayClient(api_key="secret")
    assert c._headers() == {"X-API-Key": "secret"}


# ------------------------------------------------- построение запроса (мок httpx)
def _mock_async_client(captured: dict, *, status=200, json_body=None, retry_after=None):
    """Подменить httpx.AsyncClient: захватить аргументы request и вернуть фейк-ответ."""
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = json_body if json_body is not None else {"ok": True, "response": []}
    resp.headers = {"Retry-After": retry_after} if retry_after else {}

    async def _request(method, url, **kwargs):
        captured["method"] = method
        captured["url"] = url
        captured.update(kwargs)
        return resp

    inst = MagicMock()
    inst.request = AsyncMock(side_effect=_request)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=inst)
    cm.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=cm)


@pytest.mark.asyncio
async def test_community_builds_request_and_parses():
    captured: dict = {}
    c = GatewayClient(base_url="https://gw.test", api_key="K")
    with patch("gateway_mcp.client.httpx.AsyncClient", _mock_async_client(captured)):
        out = await c.community("apiclub")
    assert captured["method"] == "GET"
    assert captured["url"] == "https://gw.test/api/gateway/community"
    assert captured["params"] == {"group": "apiclub"}
    assert captured["headers"] == {"X-API-Key": "K"}
    assert out == {"ok": True, "response": []}


@pytest.mark.asyncio
async def test_call_posts_method_and_params():
    captured: dict = {}
    c = GatewayClient(base_url="https://gw.test", api_key="K")
    body = {"ok": True, "response": [{"id": 1}]}
    with patch(
        "gateway_mcp.client.httpx.AsyncClient", _mock_async_client(captured, json_body=body)
    ):
        out = await c.call("users.get", {"user_ids": "1"})
    assert captured["method"] == "POST"
    assert captured["url"] == "https://gw.test/api/gateway/call"
    assert captured["json"] == {"method": "users.get", "params": {"user_ids": "1"}}
    assert out == body


@pytest.mark.asyncio
async def test_wall_429_raises_with_retry_after():
    captured: dict = {}
    c = GatewayClient(base_url="https://gw.test", api_key="K")
    with patch(
        "gateway_mcp.client.httpx.AsyncClient",
        _mock_async_client(captured, status=429, json_body={"detail": "Quota"}, retry_after="17"),
    ):
        with pytest.raises(GatewayError) as exc:
            await c.wall(-1, count=5)
    assert "17" in str(exc.value)
    assert captured["params"] == {"owner_id": -1, "count": 5, "offset": 0}
