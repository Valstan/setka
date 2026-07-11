"""Tests for middleware/auth_gate.py — изоляция ролей operator|radar (Ф0.1).

Мини-FastAPI-приложение + TestClient; user_loader инжектится (БД не нужна).
"""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from middleware.auth_gate import AuthGateMiddleware
from modules.radar import auth

OPERATOR_HASH = auth.hash_password("op-password")
RADAR_HASH = auth.hash_password("radar-password")

OPERATOR = SimpleNamespace(
    id=1, role="operator", is_active=True, password_hash=OPERATOR_HASH, login="op"
)
RADAR = SimpleNamespace(id=2, role="radar", is_active=True, password_hash=RADAR_HASH, login="ra")
INACTIVE = SimpleNamespace(
    id=3, role="operator", is_active=False, password_hash=OPERATOR_HASH, login="off"
)

USERS = {1: OPERATOR, 2: RADAR, 3: INACTIVE}


async def _loader(uid):
    return USERS.get(uid)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("SETKA_WEB_SECRET", "gate-test-secret")
    monkeypatch.delenv("WEB_AUTH_ENABLED", raising=False)


@pytest.fixture
def client():
    app = FastAPI()
    app.add_middleware(AuthGateMiddleware, user_loader=_loader)

    @app.get("/login")
    async def login_page():
        return {"page": "login"}

    @app.get("/api/health/full")
    async def health():
        return {"ok": True}

    @app.get("/api/regions")
    async def regions():
        return {"operator": "zone"}

    @app.get("/")
    async def dashboard():
        return {"page": "dashboard"}

    @app.get("/radar")
    async def radar_page():
        return {"page": "radar"}

    @app.get("/oidc/authorize")
    async def oidc_authorize():
        return {"page": "authorize"}

    @app.get("/api/radar/sources")
    async def radar_api():
        return {"radar": "zone"}

    return TestClient(app)


def _cookie_for(user) -> dict:
    token = auth.issue_session_token(user.id, user.role, auth.password_fragment(user.password_hash))
    return {auth.SESSION_COOKIE: token}


# ─── Публичные пути ──────────────────────────────────────────────


def test_public_paths_open_without_cookie(client):
    assert client.get("/login").status_code == 200
    assert client.get("/api/health/full").status_code == 200


# ─── Неаутентифицированные ───────────────────────────────────────


def test_api_without_cookie_is_401(client):
    resp = client.get("/api/regions")
    assert resp.status_code == 401


def test_browser_get_redirects_to_login(client):
    resp = client.get("/", headers={"Accept": "text/html"}, follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"].startswith("/login?next=")


def test_garbage_cookie_is_401(client):
    client.cookies.set(auth.SESSION_COOKIE, "garbage")
    assert client.get("/api/regions").status_code == 401


def test_oidc_authorize_redirects_to_login_without_browser_accept(client):
    # front-channel GET: curl/мониторинг без Accept: text/html должен получить
    # 302 на login, а не ложный 401 (запрос trener через brain 2026-07-10).
    resp = client.get(
        "/oidc/authorize?client_id=trener&response_type=code",
        follow_redirects=False,
    )
    assert resp.status_code == 302
    loc = resp.headers["location"]
    assert loc.startswith("/login?next=")
    # query authorize-запроса сохраняется в next, чтобы после логина вернуться.
    assert "client_id" in loc and "response_type" in loc


def test_oidc_authorize_still_401_for_non_get(client):
    # POST на front-channel путь не редиректим — только GET (спек authorize=GET).
    resp = client.post("/oidc/authorize", follow_redirects=False)
    assert resp.status_code == 401


# ─── Роли ────────────────────────────────────────────────────────


def test_operator_reaches_operator_zone(client):
    client.cookies.update(_cookie_for(OPERATOR))
    assert client.get("/api/regions").status_code == 200
    assert client.get("/radar").status_code == 200  # оператору радар тоже открыт


def test_radar_reaches_radar_zone_only(client):
    client.cookies.update(_cookie_for(RADAR))
    assert client.get("/radar").status_code == 200
    assert client.get("/api/radar/sources").status_code == 200
    assert client.get("/api/regions").status_code == 403


def test_radar_browser_redirected_to_radar_from_operator_pages(client):
    client.cookies.update(_cookie_for(RADAR))
    resp = client.get("/", headers={"Accept": "text/html"}, follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/radar"


# ─── Инвалидация сессий ──────────────────────────────────────────


def test_inactive_user_is_401(client):
    client.cookies.update(_cookie_for(INACTIVE))
    assert client.get("/api/regions").status_code == 401


def test_password_change_invalidates_session(client):
    token = auth.issue_session_token(1, "operator", "stale-fragment-")
    client.cookies.set(auth.SESSION_COOKIE, token)
    assert client.get("/api/regions").status_code == 401


# ─── Kill-switch ─────────────────────────────────────────────────


def test_kill_switch_disables_gate(client, monkeypatch):
    monkeypatch.setenv("WEB_AUTH_ENABLED", "0")
    assert client.get("/api/regions").status_code == 200
