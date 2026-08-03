"""Правила захвата чужой записи при self-serve выдаче (ADR-0010).

Экосистемный ключ **один на всех** клиентов. Значит «обновить существующую
запись» по HTTP — это ровно возможность одного проекта перевесить чужого
OIDC-клиента на свой ``redirect_uri`` или перевыпустить чужой ключ шлюза.
Поэтому граница такая: создать новое — можно, тронуть существующее — только
предъявив его текущий секрет; отключённое оператором — нельзя вообще.

Здесь проверяется именно эта граница (БД мокается: логика ветвления, а не
SQLAlchemy).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from database.models import GatewayKey
from database.models_extended import OAuthClient
from modules.ecosystem.provisioning import (
    ProvisioningError,
    provision_gateway_key,
    provision_oidc_client,
)
from modules.gateway.keys import gateway_secret_prefix, hash_gateway_secret
from modules.radar.auth import hash_password

PORTAL_URI = "https://xn--80adkdyec4j.xn--p1ai/auth/esa/callback"


class _FakeSession:
    """AsyncSessionLocal stand-in (паттерн tests/test_radar/test_auth_api.py)."""

    def __init__(self, row=None):
        self._row = row
        self.added = []
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, _stmt):
        result = MagicMock()
        result.scalar_one_or_none.return_value = self._row
        return result

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed = True


def _session_factory(row=None):
    return lambda: _FakeSession(row)


def _client_row(secret="right-secret", active=True):
    row = OAuthClient(client_id="portal")
    row.name = "Портал"
    row.redirect_uris = [PORTAL_URI]
    row.allowed_scopes = "openid profile"
    row.branding = {"icon": "🏛"}
    row.client_secret_hash = hash_password(secret)
    row.is_confidential = True
    row.is_active = active
    return row


def _key_row(secret="right-key", active=True):
    """Строка ключа после миграций 074/075: хранится хэш, не значение."""
    return GatewayKey(
        name="KAZANSKAYA",
        secret_sha256=hash_gateway_secret(secret),
        secret_prefix=gateway_secret_prefix(secret),
        is_active=active,
        note=None,
    )


async def _register(row, **kwargs):
    with patch("database.connection.AsyncSessionLocal", _session_factory(row)):
        return await provision_oidc_client(
            client_id="portal",
            name="Портал",
            redirect_uris=[PORTAL_URI],
            **kwargs,
        )


async def _issue(row, **kwargs):
    with (
        patch("database.connection.AsyncSessionLocal", _session_factory(row)),
        patch("modules.gateway.usage.record_request", new=AsyncMock()),
    ):
        return await provision_gateway_key(project="KAZANSKAYA", **kwargs)


class TestOidcClientSelfServe:
    @pytest.mark.asyncio
    async def test_new_client_gets_secret_once(self):
        result = await _register(None)
        assert result.action == "created"
        assert result.secret  # показывается один раз
        assert result.details["allowed_scopes"] == "openid profile"

    @pytest.mark.asyncio
    async def test_existing_client_without_secret_is_conflict(self):
        """Занятый client_id — 409, а не молчаливый перехват чужого входа."""
        with pytest.raises(ProvisioningError) as e:
            await _register(_client_row())
        assert e.value.code == "client_exists"
        assert e.value.status == 409

    @pytest.mark.asyncio
    async def test_existing_client_with_wrong_secret_is_forbidden(self):
        with pytest.raises(ProvisioningError) as e:
            await _register(_client_row(), current_secret="wrong")
        assert e.value.status == 403

    @pytest.mark.asyncio
    async def test_owner_can_update_and_rotate(self):
        result = await _register(_client_row(), current_secret="right-secret", rotate_secret=True)
        assert result.action == "rotated"
        assert result.secret

    @pytest.mark.asyncio
    async def test_disabled_client_is_not_resurrected(self):
        """Деактивация — решение оператора; старый секрет её не отменяет."""
        with pytest.raises(ProvisioningError) as e:
            await _register(_client_row(active=False), current_secret="right-secret")
        assert e.value.code == "client_disabled"

    @pytest.mark.asyncio
    async def test_cli_updates_without_secret(self):
        """allow_update=True — путь скрипта: root на хосте и так может всё."""
        result = await _register(_client_row(active=False), allow_update=True)
        assert result.action == "updated"
        assert result.secret is None  # без --rotate-secret секрет не трогаем


class TestGatewayKeySelfServe:
    @pytest.mark.asyncio
    async def test_new_project_gets_key(self):
        result = await _issue(None, note="заявка Казанской")
        assert result.action == "created"
        assert result.secret

    @pytest.mark.asyncio
    async def test_existing_key_without_secret_is_conflict(self):
        with pytest.raises(ProvisioningError) as e:
            await _issue(_key_row())
        assert e.value.code == "key_exists"
        assert e.value.status == 409

    @pytest.mark.asyncio
    async def test_existing_key_with_wrong_secret_is_forbidden(self):
        with pytest.raises(ProvisioningError) as e:
            await _issue(_key_row(), current_secret="wrong-key", rotate=True)
        assert e.value.status == 403

    @pytest.mark.asyncio
    async def test_owner_rotates_with_current_key(self):
        result = await _issue(_key_row(), current_secret="right-key", rotate=True)
        assert result.action == "rotated"
        assert result.secret and result.secret != "right-key"

    @pytest.mark.asyncio
    async def test_disabled_key_is_not_resurrected(self):
        with pytest.raises(ProvisioningError) as e:
            await _issue(_key_row(active=False), current_secret="right-key")
        assert e.value.code == "key_disabled"

    @pytest.mark.asyncio
    async def test_cli_can_disable_and_enable(self):
        disabled = await _issue(_key_row(), allow_update=True, set_active=False)
        assert disabled.action == "disabled"
        assert disabled.details["active"] is False

        enabled = await _issue(_key_row(active=False), allow_update=True, set_active=True)
        assert enabled.action == "enabled"
        assert enabled.details["active"] is True

    @pytest.mark.asyncio
    async def test_repeat_without_rotate_keeps_secret(self):
        """Идемпотентность: повтор заявки не перевыпускает рабочий ключ."""
        result = await _issue(_key_row(), current_secret="right-key")
        assert result.action == "unchanged"
        assert result.secret is None
