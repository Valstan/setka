"""Tests for modules/radar/auth.py — пароли (scrypt) и сессионные токены (Ф0.1)."""

import pytest

from modules.radar import auth

# ─── Пароли ──────────────────────────────────────────────────────


def test_hash_verify_roundtrip():
    stored = auth.hash_password("correct horse battery staple")
    assert stored.startswith("scrypt$")
    assert auth.verify_password("correct horse battery staple", stored)


def test_verify_rejects_wrong_password():
    stored = auth.hash_password("right")
    assert not auth.verify_password("wrong", stored)


def test_verify_rejects_garbage_stored_value():
    """Мусор в БД (битый формат) → False, не исключение."""
    assert not auth.verify_password("x", "")
    assert not auth.verify_password("x", "not-a-hash")
    assert not auth.verify_password("x", "md5$1$2$3$AAAA$BBBB")


def test_hashes_are_salted():
    """Два хэша одного пароля различаются (случайная соль)."""
    assert auth.hash_password("same") != auth.hash_password("same")


def test_password_fragment_changes_with_hash():
    """Фрагмент привязан к хэшу: смена пароля инвалидирует сессии."""
    a = auth.password_fragment(auth.hash_password("one"))
    b = auth.password_fragment(auth.hash_password("two"))
    assert a != b
    assert len(a) == 12


# ─── Сессионные токены ───────────────────────────────────────────


@pytest.fixture(autouse=True)
def _fixed_secret(monkeypatch):
    monkeypatch.setenv("SETKA_WEB_SECRET", "test-secret-key")


def test_token_roundtrip():
    token = auth.issue_session_token(7, "operator", "abcdef123456")
    payload = auth.verify_session_token(token)
    assert payload == {
        "uid": 7,
        "role": "operator",
        "pf": "abcdef123456",
        "exp": payload["exp"],
    }


def test_token_expiry():
    token = auth.issue_session_token(1, "radar", "pf", ttl_seconds=10, _now=1000.0)
    assert auth.verify_session_token(token, _now=1005.0) is not None
    assert auth.verify_session_token(token, _now=1011.0) is None


def test_token_signature_tamper():
    token = auth.issue_session_token(1, "radar", "pf")
    payload_b64, sig = token.split(".")
    # Подменяем payload — подпись перестаёт сходиться.
    forged = payload_b64[:-2] + "xx." + sig
    assert auth.verify_session_token(forged) is None


def test_token_garbage():
    assert auth.verify_session_token("") is None
    assert auth.verify_session_token("just-garbage") is None
    assert auth.verify_session_token("a.b.c") is None


def test_token_rejects_other_secret(monkeypatch):
    token = auth.issue_session_token(1, "radar", "pf")
    monkeypatch.setenv("SETKA_WEB_SECRET", "different-secret")
    assert auth.verify_session_token(token) is None
