"""Тесты VK-шлюза (web/api/gateway.py) — auth, квота, allowlist, fallthrough.

Мини-FastAPI + TestClient; VK/Redis/БД замоканы (без сети и инфры).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from modules.gateway.quota import GatewayQuota
from modules.vk_token_router import TokenCandidate
from web.api import gateway as gw

API_KEY = "s3cret-test-key"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("GATEWAY_KEY_TEST", API_KEY)
    monkeypatch.delenv("GATEWAY_DISABLED", raising=False)


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(gw.router, prefix="/api/gateway")
    return TestClient(app)


# --- авторизация --------------------------------------------------------
def test_missing_key_401(client):
    r = client.post("/api/gateway/call", json={"method": "wall.get"})
    assert r.status_code == 401


def test_wrong_key_401(client):
    r = client.post(
        "/api/gateway/call",
        json={"method": "wall.get"},
        headers={"X-API-Key": "nope"},
    )
    assert r.status_code == 401


# --- allowlist ----------------------------------------------------------
def test_disallowed_method_400(client):
    r = client.post(
        "/api/gateway/call",
        json={"method": "wall.post", "params": {"message": "spam"}},
        headers={"X-API-Key": API_KEY},
    )
    assert r.status_code == 400
    assert "not allowed" in r.json()["detail"]


# --- happy path (VK замокан) -------------------------------------------
def test_happy_path(client):
    fake_read = AsyncMock(return_value={"ok": True, "response": {"items": [1, 2, 3]}})
    with patch.object(gw, "_gateway_vk_read", fake_read):
        r = client.post(
            "/api/gateway/call",
            json={"method": "wall.get", "params": {"owner_id": -1, "count": 3}},
            headers={"X-API-Key": API_KEY},
        )
    assert r.status_code == 200
    assert r.json() == {"ok": True, "response": {"items": [1, 2, 3]}}
    fake_read.assert_awaited_once_with("wall.get", {"owner_id": -1, "count": 3})


def test_community_convenience_endpoint(client):
    fake_read = AsyncMock(return_value={"ok": True, "response": [{"id": 1}]})
    with patch.object(gw, "_gateway_vk_read", fake_read):
        r = client.get("/api/gateway/community?group=apiclub", headers={"X-API-Key": API_KEY})
    assert r.status_code == 200
    method, params = fake_read.await_args.args
    assert method == "groups.getById"
    assert params["group_ids"] == "apiclub"


# --- квота --------------------------------------------------------------
def test_quota_exceeded_429(client):
    with patch.object(GatewayQuota, "check_and_consume", lambda self, key: (False, 42)):
        r = client.post(
            "/api/gateway/call",
            json={"method": "wall.get"},
            headers={"X-API-Key": API_KEY},
        )
    assert r.status_code == 429
    assert r.headers["Retry-After"] == "42"


# --- kill-switch --------------------------------------------------------
def test_gateway_disabled_503(client, monkeypatch):
    monkeypatch.setenv("GATEWAY_DISABLED", "1")
    r = client.post(
        "/api/gateway/call",
        json={"method": "wall.get"},
        headers={"X-API-Key": API_KEY},
    )
    assert r.status_code == 503


# --- executor: cooldown-код 5 → следующий токен -------------------------
@pytest.mark.asyncio
async def test_vk_error_5_falls_through_to_next_token():
    """Первый токен отдаёт VK error 5 → report_error + переход на второй."""

    class _FakePolicy:
        def __init__(self, _session):
            self.errors = []
            self.successes = []

        async def pick(self, _op):
            return [
                TokenCandidate(name="A", token="tokA", source="user"),
                TokenCandidate(name="B", token="tokB", source="user"),
            ]

        async def report_error(self, name, code):
            self.errors.append((name, code))

        async def report_success(self, name):
            self.successes.append(name)

    fake_policy = _FakePolicy(None)

    class _FakeSessionCM:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *a):
            return False

    fake_vk = MagicMock()
    fake_vk.api_call.side_effect = [
        {"error": {"error_code": 5, "error_msg": "invalid token"}},
        {"items": [{"id": 99}]},
    ]

    with (
        patch.object(gw, "AsyncSessionLocal", return_value=_FakeSessionCM()),
        patch.object(gw, "TokenPolicy", return_value=fake_policy),
        patch("modules.vk_monitor.vk_client.VKClient", return_value=fake_vk),
    ):
        result = await gw._gateway_vk_read("wall.get", {"owner_id": -1})

    assert result == {"ok": True, "response": {"items": [{"id": 99}]}}
    assert fake_policy.errors == [("A", 5)]  # первый токен зафиксировал ошибку
    assert fake_policy.successes == ["B"]  # второй отработал
    assert fake_vk.api_call.call_count == 2


# --- executor: нет токенов → 503 ---------------------------------------
@pytest.mark.asyncio
async def test_no_tokens_503():
    from fastapi import HTTPException

    class _EmptyPolicy:
        def __init__(self, _s):
            pass

        async def pick(self, _op):
            return []

    class _FakeSessionCM:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *a):
            return False

    with (
        patch.object(gw, "AsyncSessionLocal", return_value=_FakeSessionCM()),
        patch.object(gw, "TokenPolicy", return_value=_EmptyPolicy(None)),
    ):
        with pytest.raises(HTTPException) as exc:
            await gw._gateway_vk_read("wall.get", {})
    assert exc.value.status_code == 503
