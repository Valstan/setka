"""RP-initiated logout ЕСА (web/api/radar_id.py::/oidc/logout) — маршрут целиком.

Держим три свойства: кука домена гасится всегда; возврат к клиенту — только в его
зону (иначе своя страница входа, не 4xx и не open-redirect); подсказка из
id_token_hint заменяет client_id.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from web.api import radar_id as api

CLIENT = SimpleNamespace(
    client_id="portal", redirect_uris=["https://portal.test/api/auth/callback"]
)


class _Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


async def _get_client(session, client_id):
    return CLIENT if client_id == "portal" else None


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("SESSION_COOKIE_DOMAIN", ".example.test")
    app = FastAPI()
    app.include_router(api.router)
    with (
        patch.object(api, "AsyncSessionLocal", _Session),
        patch.object(api.service, "get_client", _get_client),
        patch.object(api, "_enforce_ip_rate", lambda *a, **k: None),
    ):
        yield TestClient(app)


def _cookie_cleared(resp) -> bool:
    sc = resp.headers.get("set-cookie", "")
    return "setka_session" in sc.lower() and ("max-age=0" in sc.lower() or "expires=" in sc.lower())


def test_logout_returns_to_client_zone_with_state(client):
    r = client.get(
        "/oidc/logout",
        params={
            "client_id": "portal",
            "post_logout_redirect_uri": "https://portal.test/bye",
            "state": "s1",
        },
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert r.headers["location"] == "https://portal.test/bye?state=s1"
    assert _cookie_cleared(r)


def test_logout_foreign_uri_lands_on_login_not_error(client):
    r = client.get(
        "/oidc/logout",
        params={"client_id": "portal", "post_logout_redirect_uri": "https://evil.test/"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert r.headers["location"] == "/login?logged_out=1"
    assert _cookie_cleared(r)


def test_logout_without_client_lands_on_login(client):
    r = client.get(
        "/oidc/logout",
        params={"post_logout_redirect_uri": "https://portal.test/"},
        follow_redirects=False,
    )
    assert r.headers["location"] == "/login?logged_out=1"


def test_logout_post_form_and_id_token_hint(client):
    with patch.object(api.service, "client_id_from_id_token_hint", lambda hint: "portal"):
        r = client.post(
            "/oidc/logout",
            data={"id_token_hint": "x.y.z", "post_logout_redirect_uri": "https://portal.test/"},
            follow_redirects=False,
        )
    assert r.status_code == 302
    assert r.headers["location"] == "https://portal.test/"
