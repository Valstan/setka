"""Tests OIDC-ядра Радар-ID: code-flow, PKCE, refresh-ротация, userinfo."""

from __future__ import annotations

import time

import pytest
from authlib.jose import jwt
from authlib.oauth2.rfc7636 import create_s256_code_challenge

from database.models_extended import OAuthClient, RadarUser
from modules.radar.auth import hash_password
from modules.radar_id import service
from modules.radar_id.keys import get_public_jwks
from modules.radar_id.service import OidcError

REDIRECT = "https://client.test/auth/callback"
VERIFIER = "a" * 48
CHALLENGE = create_s256_code_challenge(VERIFIER)


async def _seed(db_session, *, confidential=True, scopes="openid profile email"):
    client = OAuthClient(
        client_id="trener",
        client_secret_hash=hash_password("s3cret") if confidential else None,
        name="Тренер",
        redirect_uris=[REDIRECT],
        allowed_scopes=scopes,
        is_confidential=confidential,
    )
    user = RadarUser(
        login="vali",
        password_hash=hash_password("pw"),
        role="radar",
        email="vali@example.test",
        email_verified=True,
        display_name="Валентин",
    )
    db_session.add_all([client, user])
    await db_session.commit()
    return client, user


async def _issue_code(db_session, client, user, **kw):
    params = dict(
        client=client,
        user=user,
        redirect_uri=REDIRECT,
        scope="openid profile email",
        code_challenge=CHALLENGE,
        code_challenge_method="S256",
        nonce="n0nce",
    )
    params.update(kw)
    return await service.issue_auth_code(db_session, **params)


# ───────── валидации ─────────


@pytest.mark.asyncio
async def test_redirect_uri_must_match_exactly(db_session, rsa_key_env):
    client, _ = await _seed(db_session)
    with pytest.raises(OidcError):
        service.validate_redirect_uri(client, REDIRECT + "/")
    service.validate_redirect_uri(client, REDIRECT)  # не бросает


@pytest.mark.asyncio
async def test_scope_is_intersected_with_client_allowlist(db_session, rsa_key_env):
    client, _ = await _seed(db_session, scopes="openid profile")
    granted = service.resolve_scope(client, "openid profile email")
    assert granted == "openid profile"  # email срезан потолком клиента
    with pytest.raises(OidcError):
        service.resolve_scope(client, "profile")  # без openid


@pytest.mark.asyncio
async def test_public_client_requires_pkce(db_session, rsa_key_env):
    client, user = await _seed(db_session, confidential=False)
    with pytest.raises(OidcError) as e:
        await _issue_code(db_session, client, user, code_challenge=None)
    assert "PKCE" in str(e.value)


@pytest.mark.asyncio
async def test_client_auth_bad_secret_rejected(db_session, rsa_key_env):
    await _seed(db_session)
    with pytest.raises(OidcError) as e:
        await service.authenticate_client(db_session, "trener", "wrong")
    assert e.value.error == "invalid_client"


# ───────── полный code-flow ─────────


@pytest.mark.asyncio
async def test_full_code_flow_id_token_and_userinfo(db_session, rsa_key_env):
    client, user = await _seed(db_session)
    code = await _issue_code(db_session, client, user)

    bundle = await service.exchange_code(
        db_session, client=client, raw_code=code, redirect_uri=REDIRECT, code_verifier=VERIFIER
    )

    claims = jwt.decode(bundle.id_token, get_public_jwks())
    claims.validate(now=int(time.time()))
    assert claims["iss"] == service.get_issuer()
    assert claims["aud"] == "trener"
    assert claims["sub"] == user.sub
    assert claims["nonce"] == "n0nce"
    assert claims["email"] == "vali@example.test"
    assert claims["email_verified"] is True
    assert claims["name"] == "Валентин"

    info = await service.userinfo(db_session, bundle.access_token)
    assert info == {
        "sub": user.sub,
        "email": "vali@example.test",
        "email_verified": True,
        "name": "Валентин",
    }


@pytest.mark.asyncio
async def test_scope_limits_claims(db_session, rsa_key_env):
    client, user = await _seed(db_session)
    code = await _issue_code(db_session, client, user, scope="openid")
    bundle = await service.exchange_code(
        db_session, client=client, raw_code=code, redirect_uri=REDIRECT, code_verifier=VERIFIER
    )
    claims = jwt.decode(bundle.id_token, get_public_jwks())
    assert "email" not in claims and "name" not in claims
    info = await service.userinfo(db_session, bundle.access_token)
    assert info == {"sub": user.sub}


@pytest.mark.asyncio
async def test_code_is_single_use(db_session, rsa_key_env):
    client, user = await _seed(db_session)
    code = await _issue_code(db_session, client, user)
    await service.exchange_code(
        db_session, client=client, raw_code=code, redirect_uri=REDIRECT, code_verifier=VERIFIER
    )
    with pytest.raises(OidcError) as e:
        await service.exchange_code(
            db_session, client=client, raw_code=code, redirect_uri=REDIRECT, code_verifier=VERIFIER
        )
    assert "already used" in e.value.description


@pytest.mark.asyncio
async def test_pkce_wrong_verifier_rejected(db_session, rsa_key_env):
    client, user = await _seed(db_session)
    code = await _issue_code(db_session, client, user)
    with pytest.raises(OidcError) as e:
        await service.exchange_code(
            db_session, client=client, raw_code=code, redirect_uri=REDIRECT, code_verifier="b" * 48
        )
    assert "PKCE" in e.value.description


@pytest.mark.asyncio
async def test_redirect_mismatch_on_exchange_rejected(db_session, rsa_key_env):
    client, user = await _seed(db_session)
    code = await _issue_code(db_session, client, user)
    with pytest.raises(OidcError):
        await service.exchange_code(
            db_session,
            client=client,
            raw_code=code,
            redirect_uri="https://evil.test/cb",
            code_verifier=VERIFIER,
        )


# ───────── refresh: ротация + reuse-detection ─────────


@pytest.mark.asyncio
async def test_refresh_rotates_and_reuse_revokes_family(db_session, rsa_key_env):
    client, user = await _seed(db_session)
    code = await _issue_code(db_session, client, user)
    bundle = await service.exchange_code(
        db_session, client=client, raw_code=code, redirect_uri=REDIRECT, code_verifier=VERIFIER
    )

    rotated = await service.refresh_grant(
        db_session, client=client, raw_refresh=bundle.refresh_token
    )
    assert rotated.refresh_token != bundle.refresh_token

    # Reuse СТАРОГО (уже ротированного) токена → invalid_grant + отзыв family.
    with pytest.raises(OidcError) as e:
        await service.refresh_grant(db_session, client=client, raw_refresh=bundle.refresh_token)
    assert "reuse" in e.value.description

    # Новый токен из той же family тоже отозван.
    with pytest.raises(OidcError):
        await service.refresh_grant(db_session, client=client, raw_refresh=rotated.refresh_token)


@pytest.mark.asyncio
async def test_userinfo_rejects_garbage_token(db_session, rsa_key_env):
    await _seed(db_session)
    with pytest.raises(OidcError):
        await service.userinfo(db_session, "not.a.jwt")


# ───────── discovery ─────────


def test_discovery_document_shape(rsa_key_env):
    doc = service.discovery_document()
    iss = doc["issuer"]
    assert doc["authorization_endpoint"] == f"{iss}/oidc/authorize"
    assert doc["token_endpoint"] == f"{iss}/oidc/token"
    assert doc["jwks_uri"] == f"{iss}/.well-known/jwks.json"
    assert doc["code_challenge_methods_supported"] == ["S256"]
    assert doc["id_token_signing_alg_values_supported"] == ["RS256"]
