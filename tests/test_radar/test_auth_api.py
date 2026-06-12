"""Tests for web/api/auth.py — login/logout/register с мокнутой сессией БД."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException, Response

from modules.radar import auth as auth_core
from web.api import auth as auth_api


class _FakeSession:
    """Minimal AsyncSessionLocal stand-in (паттерн test_templates.py)."""

    def __init__(self, *, scalar_result=None):
        self._scalar_result = scalar_result
        self.added = []
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, _stmt):
        result = MagicMock()
        result.scalar_one_or_none.return_value = self._scalar_result
        return result

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed = True

    async def refresh(self, obj):
        obj.id = obj.id or 42


class _User:
    def __init__(self, password="secret-password", role="operator", active=True):
        self.id = 1
        self.login = "valstan"
        self.password_hash = auth_core.hash_password(password)
        self.role = role
        self.is_active = active
        self.last_login_at = None


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setenv("SETKA_WEB_SECRET", "api-test-secret")


@pytest.mark.asyncio
async def test_login_success_sets_cookie():
    user = _User()
    fake = _FakeSession(scalar_result=user)
    response = Response()
    with patch.object(auth_api, "AsyncSessionLocal", lambda: fake):
        body = auth_api.LoginIn(login="valstan", password="secret-password")
        result = await auth_api.login(body, response)
    assert result == {"ok": True, "role": "operator", "login": "valstan"}
    assert fake.committed
    assert user.last_login_at is not None
    assert auth_core.SESSION_COOKIE in response.headers.get("set-cookie", "")


@pytest.mark.asyncio
async def test_login_wrong_password_is_401():
    fake = _FakeSession(scalar_result=_User())
    with patch.object(auth_api, "AsyncSessionLocal", lambda: fake):
        with pytest.raises(HTTPException) as exc:
            await auth_api.login(auth_api.LoginIn(login="valstan", password="nope"), Response())
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_login_unknown_user_is_401():
    fake = _FakeSession(scalar_result=None)
    with patch.object(auth_api, "AsyncSessionLocal", lambda: fake):
        with pytest.raises(HTTPException) as exc:
            await auth_api.login(auth_api.LoginIn(login="ghost", password="x"), Response())
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_login_inactive_user_is_401():
    fake = _FakeSession(scalar_result=_User(active=False))
    with patch.object(auth_api, "AsyncSessionLocal", lambda: fake):
        with pytest.raises(HTTPException) as exc:
            await auth_api.login(
                auth_api.LoginIn(login="valstan", password="secret-password"), Response()
            )
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_register_disabled_without_invite_env(monkeypatch):
    monkeypatch.delenv("RADAR_INVITE_CODE", raising=False)
    with pytest.raises(HTTPException) as exc:
        await auth_api.register(
            auth_api.RegisterIn(login="newuser", password="12345678", invite_code="x"),
            Response(),
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_register_wrong_invite_is_403(monkeypatch):
    monkeypatch.setenv("RADAR_INVITE_CODE", "right-code")
    with pytest.raises(HTTPException) as exc:
        await auth_api.register(
            auth_api.RegisterIn(login="newuser", password="12345678", invite_code="wrong"),
            Response(),
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_register_creates_radar_user(monkeypatch):
    monkeypatch.setenv("RADAR_INVITE_CODE", "right-code")
    fake = _FakeSession(scalar_result=None)  # логин свободен
    response = Response()
    with patch.object(auth_api, "AsyncSessionLocal", lambda: fake):
        result = await auth_api.register(
            auth_api.RegisterIn(login="newuser", password="12345678", invite_code="right-code"),
            response,
        )
    assert result["ok"] is True
    assert result["role"] == "radar"
    assert fake.added and fake.added[0].role == "radar"  # эскалации до operator нет
    assert fake.committed


@pytest.mark.asyncio
async def test_register_busy_login_is_409(monkeypatch):
    monkeypatch.setenv("RADAR_INVITE_CODE", "right-code")
    fake = _FakeSession(scalar_result=7)  # id существующего
    with patch.object(auth_api, "AsyncSessionLocal", lambda: fake):
        with pytest.raises(HTTPException) as exc:
            await auth_api.register(
                auth_api.RegisterIn(login="newuser", password="12345678", invite_code="right-code"),
                Response(),
            )
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_logout_deletes_cookie():
    response = Response()
    result = await auth_api.logout(response)
    assert result == {"ok": True}
    set_cookie = response.headers.get("set-cookie", "")
    assert auth_core.SESSION_COOKIE in set_cookie  # expired-cookie выставлена


def test_register_in_validates_login_charset():
    with pytest.raises(Exception):
        auth_api.RegisterIn(login="bad login!", password="12345678", invite_code="x")


def test_register_in_requires_min_password():
    with pytest.raises(Exception):
        auth_api.RegisterIn(login="okname", password="short", invite_code="x")
