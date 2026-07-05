"""Tests for Радар-ID key management + config + schema models (Ф1, ступень 1)."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

import database.models  # noqa: F401 — нужен mapper'ам models_extended (Region и др.)
from config import radar_id as cfg
from database.models_extended import OAuthClient, _new_sub
from modules.radar_id import keys as keys_mod


@pytest.fixture()
def tmp_rsa_key_file(tmp_path):
    """Свежий RSA-2048 PEM во временном файле + сброс lru_cache."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    path = tmp_path / "radar_id_rs256.pem"
    path.write_bytes(pem)
    keys_mod._load_private_jwk.cache_clear()
    with patch.dict(os.environ, {"RADAR_ID_PRIVATE_KEY_FILE": str(path)}):
        yield str(path)
    keys_mod._load_private_jwk.cache_clear()


def test_signing_key_loads_and_kid_is_stable(tmp_rsa_key_file):
    key = keys_mod.get_signing_key()
    kid1 = keys_mod.get_kid()
    keys_mod._load_private_jwk.cache_clear()
    kid2 = keys_mod.get_kid()
    assert key is not None
    assert kid1 == kid2  # RFC 7638 thumbprint детерминирован по ключу
    assert keys_mod.keys_available() is True


def test_public_jwks_has_no_private_material(tmp_rsa_key_file):
    jwks = keys_mod.get_public_jwks()
    assert len(jwks["keys"]) == 1
    jwk = jwks["keys"][0]
    assert jwk["kty"] == "RSA"
    assert jwk["alg"] == "RS256"
    assert jwk["use"] == "sig"
    assert "kid" in jwk
    # Приватные компоненты RSA не должны утечь в публичный JWKS.
    for private_field in ("d", "p", "q", "dp", "dq", "qi"):
        assert private_field not in jwk


def test_missing_key_file_raises_and_available_false():
    keys_mod._load_private_jwk.cache_clear()
    with patch.dict(os.environ, {"RADAR_ID_PRIVATE_KEY_FILE": "Z:/nonexistent/key.pem"}):
        assert keys_mod.keys_available() is False
        with pytest.raises(keys_mod.RadarIdKeyError):
            keys_mod.get_signing_key()
    keys_mod._load_private_jwk.cache_clear()


def test_invalid_pem_raises(tmp_path):
    bad = tmp_path / "bad.pem"
    bad.write_text("not a pem")
    keys_mod._load_private_jwk.cache_clear()
    with patch.dict(os.environ, {"RADAR_ID_PRIVATE_KEY_FILE": str(bad)}):
        with pytest.raises(keys_mod.RadarIdKeyError):
            keys_mod.get_signing_key()
    keys_mod._load_private_jwk.cache_clear()


# ───────── config ─────────


def test_issuer_default_is_punycode_and_stripped():
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("RADAR_ID_ISSUER", None)
        assert cfg.get_issuer() == "https://xn--b1ae3a1a.xn--80adkdyec4j.xn--p1ai"
    with patch.dict(os.environ, {"RADAR_ID_ISSUER": "https://example.test/"}):
        assert cfg.get_issuer() == "https://example.test"


def test_ttl_defaults_and_bad_values():
    with patch.dict(os.environ, {"RADAR_ID_ACCESS_TOKEN_TTL": "junk"}):
        assert cfg.get_access_token_ttl() == 600
    with patch.dict(os.environ, {"RADAR_ID_AUTH_CODE_TTL": "30"}):
        assert cfg.get_auth_code_ttl() == 30
    assert cfg.get_refresh_ttl_days() == 30


def test_kill_switch():
    with patch.dict(os.environ, {"RADAR_ID_DISABLED": "1"}):
        assert cfg.radar_id_disabled() is True
    with patch.dict(os.environ, {"RADAR_ID_DISABLED": "0"}):
        assert cfg.radar_id_disabled() is False


# ───────── models ─────────


def test_new_sub_is_uuid4_string():
    s = _new_sub()
    assert isinstance(s, str)
    assert len(s) == 36
    assert s.count("-") == 4


def test_oauth_client_scope_list():
    c = OAuthClient(client_id="trener", name="trener", allowed_scopes="openid profile email")
    assert c.scope_list() == ["openid", "profile", "email"]
    c2 = OAuthClient(client_id="x", name="x", allowed_scopes="")
    assert c2.scope_list() == []
