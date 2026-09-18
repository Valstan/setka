"""Маршрут `/oidc/authorize` целиком: что реально уезжает клиенту и человеку.

Чистые функции разбора и решения проверены в `test_prompt_and_max_age.py`;
здесь — проводка, где ошибка не видна ни одному юнит-тесту:

* `prompt=none` у гостя обязан вернуть `login_required` **клиенту**, а не увести
  человека на форму входа (для клиента это зависший iframe вместо ответа);
* `prompt=login` обязан увести на /login и вернуться на ТОТ ЖЕ authorize с
  подписанной меткой — иначе петля;
* вернувшись с меткой и свежим входом, человек обязан получить `code`, а не
  второй круг;
* `consent` (экрана нет) уезжает клиенту отказом, а не выдачей кода.

Сессия здесь подделывается тестовой middleware — ровно то, что в бою кладёт
AuthGate: `request.state.user` и `request.state.session_iat`.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from web.api import radar_id as api

REDIRECT = "https://portal.test/api/auth/callback"
CLIENT = SimpleNamespace(
    client_id="portal",
    redirect_uris=[REDIRECT],
    is_confidential=True,
    scope_list=lambda: ["openid", "profile", "email"],
)
USER = SimpleNamespace(id=1, sub="sub-1", login="visitor", is_active=True)

# Когда «человек вошёл»: тесты двигают эту величину, middleware её отдаёт.
SESSION = {"iat": None}


class _Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


async def _get_client(session, client_id):
    return CLIENT if client_id == "portal" else None


async def _issue_code(session, **kw):
    _issue_code.calls.append(kw)
    return "the-code"


_issue_code.calls = []


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("SETKA_WEB_SECRET", "test-secret-key")
    _issue_code.calls.clear()
    SESSION["iat"] = None

    app = FastAPI()

    @app.middleware("http")
    async def fake_auth_gate(request: Request, call_next):
        if SESSION["iat"] is not None:
            request.state.user = USER
            request.state.session_iat = SESSION["iat"]
        return await call_next(request)

    app.include_router(api.router)
    with (
        patch.object(api, "AsyncSessionLocal", _Session),
        patch.object(api.service, "get_client", _get_client),
        patch.object(api.service, "issue_auth_code", _issue_code),
        patch.object(api, "_enforce_ip_rate", lambda *a, **k: None),
    ):
        yield TestClient(app)


def _authorize(client, **extra):
    params = {
        "response_type": "code",
        "client_id": "portal",
        "redirect_uri": REDIRECT,
        "scope": "openid profile",
        "state": "s1",
    }
    params.update(extra)
    return client.get("/oidc/authorize", params=params, follow_redirects=False)


def _location(resp):
    return urlparse(resp.headers["location"])


def _query(resp):
    return parse_qs(_location(resp).query)


def test_guest_with_prompt_none_gets_login_required(client):
    """Клиент запретил UI — отвечаем ему, а не ведём человека на форму."""
    resp = _authorize(client, prompt="none")
    assert resp.status_code == 302
    loc = _location(resp)
    assert loc.netloc == "portal.test"  # ответ уехал КЛИЕНТУ
    assert _query(resp)["error"] == ["login_required"]
    assert _query(resp)["state"] == ["s1"]
    assert not _issue_code.calls


def test_guest_with_prompt_none_and_unknown_client_is_400(client):
    """Отвечать «клиенту» некуда, пока клиент не проверен — иначе open-redirect."""
    resp = _authorize(client, prompt="none", client_id="stranger")
    assert resp.status_code == 400


def test_live_session_without_requirements_gets_code(client):
    SESSION["iat"] = int(time.time()) - 3600
    resp = _authorize(client)
    assert _query(resp)["code"] == ["the-code"]


def test_prompt_login_sends_to_login_with_signed_marker(client):
    """Живая сессия требование не выполняет: уводим переспросить."""
    SESSION["iat"] = int(time.time()) - 3600
    resp = _authorize(client, prompt="login")
    loc = _location(resp)
    assert loc.path == "/login"
    assert not _issue_code.calls

    back = parse_qs(loc.query)["next"][0]
    back_q = parse_qs(urlparse(back).query)
    assert urlparse(back).path == "/oidc/authorize"
    # Возврат — на тот же запрос клиента, со всеми его параметрами.
    assert back_q["client_id"] == ["portal"] and back_q["state"] == ["s1"]
    assert back_q["prompt"] == ["login"]
    # …плюс подписанная точка отсчёта.
    assert api.read_reauth_marker(back_q[api.REAUTH_PARAM][0]) is not None


def test_login_after_marker_breaks_the_loop(client):
    """Гвоздь: вошёл после просьбы — получил код, а не второй круг."""
    asked_at = time.time() - 30
    marker = api.issue_reauth_marker(_now=asked_at)
    SESSION["iat"] = int(asked_at) + 5  # вход СЛУЧИЛСЯ после просьбы

    resp = _authorize(client, prompt="login", **{api.REAUTH_PARAM: marker})
    assert _query(resp)["code"] == ["the-code"]


def test_old_session_with_marker_is_sent_back(client):
    """Вернулся с той же старой сессией — требование не выполнено."""
    asked_at = time.time() - 30
    marker = api.issue_reauth_marker(_now=asked_at)
    SESSION["iat"] = int(asked_at) - 600

    resp = _authorize(client, prompt="login", **{api.REAUTH_PARAM: marker})
    assert _location(resp).path == "/login"
    assert not _issue_code.calls


def test_stale_max_age_sends_to_login(client):
    SESSION["iat"] = int(time.time()) - 7200
    resp = _authorize(client, max_age="600")
    assert _location(resp).path == "/login"
    assert not _issue_code.calls


def test_fresh_max_age_passes_and_carries_auth_time(client):
    SESSION["iat"] = int(time.time()) - 60
    resp = _authorize(client, max_age="600")
    assert _query(resp)["code"] == ["the-code"]
    # Время входа доехало до выдачи кода — клиент просил именно его.
    assert _issue_code.calls[0]["auth_time"] is not None


def test_prompt_none_with_stale_max_age_answers_client(client):
    """Нельзя ни показать UI, ни соврать — значит честный отказ."""
    SESSION["iat"] = int(time.time()) - 7200
    resp = _authorize(client, prompt="none", max_age="600")
    assert _location(resp).netloc == "portal.test"
    assert _query(resp)["error"] == ["login_required"]


def test_consent_prompt_is_refused_to_the_client(client):
    SESSION["iat"] = int(time.time()) - 60
    resp = _authorize(client, prompt="consent")
    assert _query(resp)["error"] == ["consent_required"]
    assert not _issue_code.calls


def test_unknown_prompt_is_invalid_request(client):
    SESSION["iat"] = int(time.time()) - 60
    resp = _authorize(client, prompt="logout")
    assert _query(resp)["error"] == ["invalid_request"]
