"""Tests ВК-upstream Радар-ID (R16): state-blob, обмен кода, связывание."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from database.models_extended import RadarUser
from modules.radar.auth import hash_password
from modules.radar_id import vk_upstream as vku

APP_ENV = {"RADAR_ID_VK_APP_ID": "54999999", "SETKA_WEB_SECRET": "test-secret"}


# ───────── state-blob ─────────


def test_state_blob_roundtrip_and_tamper():
    with patch.dict(os.environ, APP_ENV):
        blob = vku.sign_oauth_state({"st": "abc", "cv": "ver", "next": "/oidc/authorize?x=1"})
        payload = vku.verify_oauth_state(blob)
        assert payload["st"] == "abc" and payload["cv"] == "ver"
        # Подмена тела → None
        body, sig = blob.split(".")
        assert vku.verify_oauth_state(body + "x." + sig) is None
        assert vku.verify_oauth_state("garbage") is None


def test_state_blob_expires():
    with patch.dict(os.environ, APP_ENV):
        blob = vku.sign_oauth_state({"st": "a"}, _now=1000.0)
        assert vku.verify_oauth_state(blob, _now=1000.0 + vku.OAUTH_STATE_TTL + 1) is None


def test_safe_next_blocks_external():
    assert vku.safe_next("/oidc/authorize?a=1") == "/oidc/authorize?a=1"
    assert vku.safe_next("https://evil.test") == "/radar"
    assert vku.safe_next("//evil.test") == "/radar"
    assert vku.safe_next(None) == "/radar"


# ───────── authorize URL ─────────


def test_build_authorize_requires_app_id():
    with patch.dict(os.environ, {"RADAR_ID_VK_APP_ID": ""}):
        with pytest.raises(vku.VkUpstreamError):
            vku.build_vk_authorize("/radar")


def test_build_authorize_url_has_pkce_and_state():
    with patch.dict(os.environ, APP_ENV):
        url, blob = vku.build_vk_authorize("/oidc/authorize?client_id=trener")
        payload = vku.verify_oauth_state(blob)
        assert url.startswith(vku.VK_AUTHORIZE_URL + "?")
        assert "code_challenge_method=S256" in url
        assert "client_id=54999999" in url
        assert f"state={payload['st']}" in url
        assert payload["next"] == "/oidc/authorize?client_id=trener"


# ───────── обмен кода / user_info (httpx замокан) ─────────


def _resp(status: int, body: dict):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = body
    return r


@pytest.mark.asyncio
async def test_exchange_sends_device_id_and_parses_token():
    client = MagicMock()
    client.post = AsyncMock(return_value=_resp(200, {"access_token": "at", "user_id": 7}))
    with patch.dict(os.environ, APP_ENV):
        out = await vku.exchange_vk_code(
            code="c", device_id="dev1", state="st", code_verifier="cv", client=client
        )
    assert out["access_token"] == "at"
    sent = client.post.call_args.kwargs["data"]
    assert sent["device_id"] == "dev1"  # грабля R16: device_id обязателен
    assert sent["code_verifier"] == "cv"
    assert "client_secret" not in sent  # PKCE заменяет secret


@pytest.mark.asyncio
async def test_exchange_error_body_raises():
    client = MagicMock()
    client.post = AsyncMock(return_value=_resp(400, {"error": "invalid_grant"}))
    with patch.dict(os.environ, APP_ENV):
        with pytest.raises(vku.VkUpstreamError) as e:
            await vku.exchange_vk_code(
                code="c", device_id="d", state="s", code_verifier="v", client=client
            )
    assert "invalid_grant" in str(e.value)


@pytest.mark.asyncio
async def test_fetch_vk_user_parses_profile():
    client = MagicMock()
    client.post = AsyncMock(
        return_value=_resp(
            200,
            {"user": {"user_id": "123", "first_name": "Иван", "last_name": "Т", "email": "i@t.ru"}},
        )
    )
    with patch.dict(os.environ, APP_ENV):
        user = await vku.fetch_vk_user("at", client=client)
    assert user["user_id"] == "123"


@pytest.mark.asyncio
async def test_fetch_vk_user_without_id_raises():
    client = MagicMock()
    client.post = AsyncMock(return_value=_resp(200, {"user": {}}))
    with patch.dict(os.environ, APP_ENV):
        with pytest.raises(vku.VkUpstreamError):
            await vku.fetch_vk_user("at", client=client)


# ───────── связывание с RadarUser ─────────


@pytest.mark.asyncio
async def test_existing_vk_user_is_returned(db_session):
    u = RadarUser(login="x", password_hash=hash_password("p"), role="radar", vk_user_id=42)
    db_session.add(u)
    await db_session.commit()
    out = await vku.find_or_create_user(db_session, {"user_id": 42, "first_name": "A"})
    assert out.id == u.id


@pytest.mark.asyncio
async def test_links_by_verified_email(db_session):
    u = RadarUser(
        login="y",
        password_hash=hash_password("p"),
        role="radar",
        email="who@t.ru",
        email_verified=True,
    )
    db_session.add(u)
    await db_session.commit()
    out = await vku.find_or_create_user(
        db_session, {"user_id": 77, "email": "WHO@t.ru", "first_name": "B"}
    )
    assert out.id == u.id
    assert out.vk_user_id == 77


@pytest.mark.asyncio
async def test_does_not_link_unverified_email_creates_new(db_session):
    u = RadarUser(
        login="z",
        password_hash=hash_password("p"),
        role="radar",
        email="who@t.ru",
        email_verified=False,  # анти-захват: не привязываем
    )
    db_session.add(u)
    await db_session.commit()
    out = await vku.find_or_create_user(db_session, {"user_id": 88, "email": "who@t.ru"})
    assert out.id != u.id
    assert out.vk_user_id == 88


@pytest.mark.asyncio
async def test_creates_soc_only_user(db_session):
    out = await vku.find_or_create_user(
        db_session, {"user_id": 99, "first_name": "Пётр", "last_name": "И", "email": "p@t.ru"}
    )
    assert out.login is None and out.password_hash is None
    assert out.role == "radar"
    assert out.email_verified is True
    assert out.display_name == "Пётр И"
    assert len(out.sub) == 36


@pytest.mark.asyncio
async def test_inactive_linked_user_rejected(db_session):
    u = RadarUser(
        login="off", password_hash=hash_password("p"), role="radar", vk_user_id=5, is_active=False
    )
    db_session.add(u)
    await db_session.commit()
    with pytest.raises(vku.VkUpstreamError):
        await vku.find_or_create_user(db_session, {"user_id": 5})
