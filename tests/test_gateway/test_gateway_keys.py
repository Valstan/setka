"""Тесты БД-источника ключей шлюза (modules/gateway/keys.py) и агрегатного
бюджета (мандат brain 2026-07-12).

Семантика merge — как у vk_tokens (#336): БД главнее env при совпадении
имени; выключенный в БД ключ env НЕ воскрешает; env — bootstrap для имён,
которых в БД нет; недоступная БД → чистый env (аварийный fallback).
"""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from modules.gateway import keys as gwkeys
from modules.gateway.quota import GatewayQuota
from web.api import gateway as gw


def _db_with_rows(rows):
    """Замокать AsyncSessionLocal так, чтобы SELECT вернул ``rows``."""
    session = MagicMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = rows
    session.execute = AsyncMock(return_value=result)

    @asynccontextmanager
    async def _factory():
        yield session

    return _factory


def _row(name, secret, active=True):
    return SimpleNamespace(name=name, secret=secret, is_active=active)


# --- merge-семантика БД поверх env ---------------------------------------
@pytest.mark.asyncio
async def test_db_overrides_env_value(monkeypatch):
    monkeypatch.setenv("GATEWAY_KEY_ALPHA", "env-secret")
    with patch(
        "database.connection.AsyncSessionLocal", _db_with_rows([_row("ALPHA", "db-secret")])
    ):
        keys = await gwkeys.get_effective_gateway_keys()
    assert keys["ALPHA"] == "db-secret"


@pytest.mark.asyncio
async def test_db_disabled_not_resurrected_by_env(monkeypatch):
    monkeypatch.setenv("GATEWAY_KEY_ALPHA", "env-secret")
    with patch(
        "database.connection.AsyncSessionLocal",
        _db_with_rows([_row("ALPHA", "db-secret", active=False)]),
    ):
        keys = await gwkeys.get_effective_gateway_keys()
    assert "ALPHA" not in keys


@pytest.mark.asyncio
async def test_env_bootstrap_for_names_missing_in_db(monkeypatch):
    monkeypatch.setenv("GATEWAY_KEY_BETA", "env-secret")
    with patch(
        "database.connection.AsyncSessionLocal", _db_with_rows([_row("ALPHA", "db-secret")])
    ):
        keys = await gwkeys.get_effective_gateway_keys()
    assert keys == {"ALPHA": "db-secret", "BETA": "env-secret"}


@pytest.mark.asyncio
async def test_db_failure_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("GATEWAY_KEY_GAMMA", "env-secret")

    @asynccontextmanager
    async def _boom():
        raise RuntimeError("db down")
        yield  # pragma: no cover

    with patch("database.connection.AsyncSessionLocal", _boom):
        keys = await gwkeys.get_effective_gateway_keys()
    assert keys == {"GAMMA": "env-secret"}


# --- auth шлюза через БД-ключ --------------------------------------------
@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(gw.router, prefix="/api/gateway")
    return TestClient(app)


def test_auth_accepts_db_key(client, monkeypatch):
    monkeypatch.delenv("GATEWAY_KEY_DBPROJ", raising=False)
    fake_read = AsyncMock(return_value={"ok": True, "response": []})
    fake_log = AsyncMock()
    with (
        patch("database.connection.AsyncSessionLocal", _db_with_rows([_row("DBPROJ", "db-key")])),
        patch.object(GatewayQuota, "check_and_consume", lambda self, key, day=True: (True, 0)),
        patch.object(gw, "_gateway_vk_read", fake_read),
        patch("modules.gateway.usage.record_request", fake_log),
    ):
        r = client.post(
            "/api/gateway/call",
            json={"method": "wall.get", "params": {}},
            headers={"X-API-Key": "db-key"},
        )
    assert r.status_code == 200
    assert r.json()["ok"] is True


# --- агрегатный бюджет шлюза ----------------------------------------------
def test_global_budget_429(client, monkeypatch):
    """Per-consumer квота ок, но общий бюджет шлюза исчерпан → 429 quota-global."""
    monkeypatch.setenv("GATEWAY_KEY_TESTG", "kg")
    monkeypatch.setenv("GATEWAY_GLOBAL_QUOTA_PER_MIN", "10")
    fake_log = AsyncMock()

    def _consume(self, key, day=True):
        if key == "__gateway__":
            return False, 33  # общий бюджет исчерпан
        return True, 0

    with (
        patch("database.connection.AsyncSessionLocal", _db_with_rows([])),
        patch.object(GatewayQuota, "check_and_consume", _consume),
        patch("modules.gateway.usage.record_request", fake_log),
    ):
        r = client.post(
            "/api/gateway/call",
            json={"method": "wall.get", "params": {}},
            headers={"X-API-Key": "kg"},
        )
    assert r.status_code == 429
    assert r.headers["Retry-After"] == "33"
    fake_log.assert_awaited_once()
    assert fake_log.await_args.args[1] == "quota-global"


def test_global_budget_disabled_by_zero(client, monkeypatch):
    """GATEWAY_GLOBAL_QUOTA_PER_MIN=0 → слой выключен, запрос проходит."""
    monkeypatch.setenv("GATEWAY_KEY_TESTG", "kg")
    monkeypatch.setenv("GATEWAY_GLOBAL_QUOTA_PER_MIN", "0")
    fake_read = AsyncMock(return_value={"ok": True, "response": []})

    def _consume(self, key, day=True):
        assert key != "__gateway__", "глобальный слой не должен вызываться при 0"
        return True, 0

    with (
        patch("database.connection.AsyncSessionLocal", _db_with_rows([])),
        patch.object(GatewayQuota, "check_and_consume", _consume),
        patch.object(gw, "_gateway_vk_read", fake_read),
        patch("modules.gateway.usage.record_request", AsyncMock()),
    ):
        r = client.post(
            "/api/gateway/call",
            json={"method": "wall.get", "params": {}},
            headers={"X-API-Key": "kg"},
        )
    assert r.status_code == 200


# --- GatewayQuota day=False ------------------------------------------------
def test_quota_day_false_skips_day_window():
    calls = []

    def _script_factory(keys, args):
        calls.append(keys[0])
        return 1

    redis_client = MagicMock()
    redis_client.register_script.return_value = _script_factory
    q = GatewayQuota(redis_client, per_min=5, per_day=100)
    allowed, _ = q.check_and_consume("__gateway__", day=False)
    assert allowed is True
    assert all(":min:" in k for k in calls)  # суточное окно не трогали
