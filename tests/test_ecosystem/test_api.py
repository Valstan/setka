"""HTTP-слой self-serve выдачи (``/api/ecosystem``).

Проверяется то, что принадлежит именно web-слою: ключ выдачи (fail-closed при
отсутствии настройки), kill-switch, отображение отказов ядра в коды HTTP и то,
что секрет уходит в тело ответа ровно один раз.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from modules.ecosystem.provisioning import ProvisioningError, ProvisionResult
from web.api import ecosystem as api

PORTAL_URI = "https://xn--80adkdyec4j.xn--p1ai/auth/esa/callback"


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv("ECOSYSTEM_KEY", "eco-secret")
    monkeypatch.delenv("ECOSYSTEM_PROVISIONING_DISABLED", raising=False)


class TestEcosystemKeyGate:
    @pytest.mark.asyncio
    async def test_correct_key_passes(self):
        assert await api.require_ecosystem_key("eco-secret") == "ecosystem"

    @pytest.mark.asyncio
    async def test_missing_key_is_401(self):
        with pytest.raises(HTTPException) as e:
            await api.require_ecosystem_key(None)
        assert e.value.status_code == 401

    @pytest.mark.asyncio
    async def test_wrong_key_is_401(self):
        with pytest.raises(HTTPException) as e:
            await api.require_ecosystem_key("guess")
        assert e.value.status_code == 401

    @pytest.mark.asyncio
    async def test_unconfigured_is_503_not_open_door(self, monkeypatch):
        """Fail-closed: нет ключа в env — выдача недоступна, а не «пускаем всех»."""
        monkeypatch.delenv("ECOSYSTEM_KEY", raising=False)
        with pytest.raises(HTTPException) as e:
            await api.require_ecosystem_key("whatever")
        assert e.value.status_code == 503

    @pytest.mark.asyncio
    async def test_kill_switch_is_503(self, monkeypatch):
        """Утёк ключ — гасим выдачу одной переменной; выданное продолжает жить."""
        monkeypatch.setenv("ECOSYSTEM_PROVISIONING_DISABLED", "1")
        with pytest.raises(HTTPException) as e:
            await api.require_ecosystem_key("eco-secret")
        assert e.value.status_code == 503


class TestRegisterClient:
    @pytest.mark.asyncio
    async def test_returns_secret_and_smoke_guide(self):
        result = ProvisionResult(
            action="created",
            identifier="portal",
            secret="s3cret",
            details={"redirect_uris": [PORTAL_URI], "allowed_scopes": "openid profile"},
        )
        body = api.OidcClientIn(client_id="portal", name="Портал", redirect_uris=[PORTAL_URI])
        with patch.object(api, "provision_oidc_client", new=AsyncMock(return_value=result)):
            payload = await api.register_client(body, "ecosystem")
        assert payload["client_secret"] == "s3cret"
        assert payload["secret_shown_once"] is True
        # Ответ сам показывает, как проверить круг — без переписки (мандат brain).
        assert payload["smoke_guide"] == api.SMOKE_GUIDE
        assert payload["discovery"].endswith("/.well-known/openid-configuration")

    @pytest.mark.asyncio
    async def test_core_refusal_maps_to_its_status(self):
        error = ProvisioningError("client_exists", "занят", status=409)
        body = api.OidcClientIn(client_id="portal", name="Портал", redirect_uris=[PORTAL_URI])
        with patch.object(api, "provision_oidc_client", new=AsyncMock(side_effect=error)):
            with pytest.raises(HTTPException) as e:
                await api.register_client(body, "ecosystem")
        assert e.value.status_code == 409
        assert e.value.detail["error"] == "client_exists"

    @pytest.mark.asyncio
    async def test_unchanged_response_has_no_secret_field(self):
        result = ProvisionResult(action="updated", identifier="portal", secret=None, details={})
        body = api.OidcClientIn(client_id="portal", name="Портал", redirect_uris=[PORTAL_URI])
        with patch.object(api, "provision_oidc_client", new=AsyncMock(return_value=result)):
            payload = await api.register_client(body, "ecosystem")
        assert "client_secret" not in payload
        assert "secret_shown_once" not in payload


class TestIssueGatewayKey:
    @pytest.mark.asyncio
    async def test_returns_api_key_and_usage_hint(self):
        result = ProvisionResult(
            action="created", identifier="KAZANSKAYA", secret="k3y", details={"active": True}
        )
        body = api.GatewayKeyIn(project="kazanskaya", note="заявка")
        with patch.object(api, "provision_gateway_key", new=AsyncMock(return_value=result)):
            payload = await api.issue_gateway_key(body, "ecosystem")
        assert payload["api_key"] == "k3y"
        assert payload["header"] == "X-API-Key"
        assert payload["endpoint"] == "/api/gateway/call"

    @pytest.mark.asyncio
    async def test_disabled_key_refusal_is_403(self):
        error = ProvisioningError("key_disabled", "отключён", status=403)
        body = api.GatewayKeyIn(project="kazanskaya")
        with patch.object(api, "provision_gateway_key", new=AsyncMock(side_effect=error)):
            with pytest.raises(HTTPException) as e:
                await api.issue_gateway_key(body, "ecosystem")
        assert e.value.status_code == 403
