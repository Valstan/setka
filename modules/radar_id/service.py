"""OIDC-ядро Радар-ID: коды, токены, PKCE, ротация refresh (ADR-0002 Ф1).

Чистая сервис-логика поверх async-session — HTTP-слой в
``web/api/radar_id.py``. Крипта — Authlib (JWT RS256, S256-challenge),
хэш client_secret — scrypt из ``modules.radar.auth`` (тот же формат,
что пароли RadarUser).

Безопасность (MUST ADR-0002 §5):
- authorization code: single-use (``used_at``), TTL ~60с, хранится sha256;
- PKCE S256 обязателен для public-клиентов, поддержан для confidential;
- access/id_token — короткие RS256 JWT (kid в header);
- refresh: непрозрачный токен (sha256 в БД), ротация на каждый refresh,
  family-based reuse-detection: предъявление уже погашенного токена
  отзывает всю family.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from authlib.jose import jwt
from authlib.oauth2.rfc7636 import create_s256_code_challenge
from sqlalchemy import select, update

from config.radar_id import (
    SUPPORTED_SCOPES,
    get_access_token_ttl,
    get_auth_code_ttl,
    get_issuer,
    get_refresh_ttl_days,
)
from database.models_extended import OAuthAuthCode, OAuthClient, OAuthRefreshToken, RadarUser
from modules.radar.auth import verify_password
from modules.radar_id.keys import get_kid, get_public_jwks, get_signing_key

logger = logging.getLogger(__name__)
audit = logging.getLogger("radar_id.audit")


class OidcError(Exception):
    """Протокольная ошибка OIDC (код по RFC 6749 + человекочитаемое описание)."""

    def __init__(self, error: str, description: str = ""):
        super().__init__(description or error)
        self.error = error
        self.description = description


@dataclass
class TokenBundle:
    """Ответ token-эндпоинта."""

    access_token: str
    id_token: Optional[str]
    refresh_token: Optional[str]
    expires_in: int
    scope: str
    token_type: str = "Bearer"

    def as_response(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "access_token": self.access_token,
            "token_type": self.token_type,
            "expires_in": self.expires_in,
            "scope": self.scope,
        }
        if self.id_token:
            out["id_token"] = self.id_token
        if self.refresh_token:
            out["refresh_token"] = self.refresh_token
        return out


def _sha256(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _utcnow() -> datetime:
    return datetime.utcnow()


# ---------------------------------------------------------------------------
# Клиенты и scopes
# ---------------------------------------------------------------------------


async def get_client(session, client_id: str) -> Optional[OAuthClient]:
    if not client_id:
        return None
    row = (
        await session.execute(
            select(OAuthClient).where(
                OAuthClient.client_id == client_id, OAuthClient.is_active.is_(True)
            )
        )
    ).scalar_one_or_none()
    return row


async def authenticate_client(session, client_id: str, client_secret: Optional[str]) -> OAuthClient:
    """Аутентификация клиента на token-эндпоинте.

    Confidential — обязателен верный secret (scrypt-hash в БД);
    public (без secret_hash) — client_id + PKCE (проверяется на обмене кода).
    """
    client = await get_client(session, client_id)
    if client is None:
        raise OidcError("invalid_client", "unknown client_id")
    if client.is_confidential:
        if not client_secret or not client.client_secret_hash:
            raise OidcError("invalid_client", "client authentication required")
        if not verify_password(client_secret, client.client_secret_hash):
            raise OidcError("invalid_client", "bad client credentials")
    return client


def resolve_scope(client: OAuthClient, requested: str) -> str:
    """Пересечение requested ∩ allowed(client) ∩ SUPPORTED; openid обязателен.

    Порядок канонический (по SUPPORTED_SCOPES) — детерминизм для тестов/логов.
    """
    req = set((requested or "").split())
    if "openid" not in req:
        raise OidcError("invalid_scope", "scope must include openid")
    allowed = set(client.scope_list()) & set(SUPPORTED_SCOPES)
    granted = req & allowed
    if "openid" not in granted:
        raise OidcError("invalid_scope", "client is not allowed to use openid")
    return " ".join(s for s in SUPPORTED_SCOPES if s in granted)


def validate_redirect_uri(client: OAuthClient, redirect_uri: str) -> None:
    """Точное совпадение с allowlist'ом (символ-в-символ, punycode — G108)."""
    uris = client.redirect_uris or []
    if not redirect_uri or redirect_uri not in uris:
        raise OidcError("invalid_request", "redirect_uri is not registered for this client")


# ---------------------------------------------------------------------------
# Authorization code
# ---------------------------------------------------------------------------


async def issue_auth_code(
    session,
    *,
    client: OAuthClient,
    user: RadarUser,
    redirect_uri: str,
    scope: str,
    code_challenge: Optional[str],
    code_challenge_method: Optional[str],
    nonce: Optional[str],
) -> str:
    """Выдать одноразовый authorization code (возвращает сырой код)."""
    if code_challenge:
        if (code_challenge_method or "S256") != "S256":
            raise OidcError("invalid_request", "only S256 code_challenge_method is supported")
    elif not client.is_confidential:
        # Public-клиент без PKCE — запрещено (MUST).
        raise OidcError("invalid_request", "PKCE (S256) is required for public clients")

    raw = secrets.token_urlsafe(32)
    now = _utcnow()
    session.add(
        OAuthAuthCode(
            code_hash=_sha256(raw),
            client_id=client.client_id,
            user_id=user.id,
            redirect_uri=redirect_uri,
            scope=scope,
            code_challenge=code_challenge,
            code_challenge_method="S256" if code_challenge else None,
            nonce=nonce,
            auth_time=now,
            expires_at=now + timedelta(seconds=get_auth_code_ttl()),
        )
    )
    await session.commit()
    audit.info(
        "authorize: code issued client=%s sub=%s scope=%s", client.client_id, user.sub, scope
    )
    return raw


# ---------------------------------------------------------------------------
# Подпись токенов
# ---------------------------------------------------------------------------


def _sign(payload: Dict[str, Any]) -> str:
    header = {"alg": "RS256", "kid": get_kid()}
    return jwt.encode(header, payload, get_signing_key()).decode()


def _id_token_claims(
    user: RadarUser, client_id: str, scope: str, *, nonce: Optional[str], auth_time: datetime
) -> Dict[str, Any]:
    now = int(time.time())
    claims: Dict[str, Any] = {
        "iss": get_issuer(),
        "sub": user.sub,
        "aud": client_id,
        "iat": now,
        "exp": now + get_access_token_ttl(),
        "auth_time": int(auth_time.timestamp()),
    }
    if nonce:
        claims["nonce"] = nonce
    scopes = scope.split()
    # Claims-минимизация (152-ФЗ): только то, что покрыто granted-scope.
    if "email" in scopes and user.email:
        claims["email"] = user.email
        claims["email_verified"] = bool(user.email_verified)
    if "profile" in scopes:
        claims["name"] = user.display_name or user.login or ""
    return claims


def _access_token_claims(user: RadarUser, client_id: str, scope: str) -> Dict[str, Any]:
    now = int(time.time())
    return {
        "iss": get_issuer(),
        "sub": user.sub,
        "aud": get_issuer(),  # access предъявляется нашему же /userinfo
        "client_id": client_id,
        "scope": scope,
        "iat": now,
        "exp": now + get_access_token_ttl(),
        "jti": secrets.token_urlsafe(8),
    }


async def _new_refresh_token(
    session,
    *,
    user_id: int,
    client_id: str,
    scope: str,
    family_id: Optional[str] = None,
    rotated_from: Optional[int] = None,
) -> str:
    raw = secrets.token_urlsafe(48)
    session.add(
        OAuthRefreshToken(
            token_hash=_sha256(raw),
            family_id=family_id or str(uuid.uuid4()),
            user_id=user_id,
            client_id=client_id,
            scope=scope,
            rotated_from=rotated_from,
            expires_at=_utcnow() + timedelta(days=get_refresh_ttl_days()),
        )
    )
    return raw


# ---------------------------------------------------------------------------
# Grants
# ---------------------------------------------------------------------------


async def exchange_code(
    session,
    *,
    client: OAuthClient,
    raw_code: str,
    redirect_uri: str,
    code_verifier: Optional[str],
) -> TokenBundle:
    """Grant ``authorization_code``: код → id_token + access + refresh."""
    row: Optional[OAuthAuthCode] = (
        await session.execute(
            select(OAuthAuthCode).where(OAuthAuthCode.code_hash == _sha256(raw_code or ""))
        )
    ).scalar_one_or_none()
    if row is None or row.client_id != client.client_id:
        raise OidcError("invalid_grant", "unknown authorization code")
    if row.used_at is not None:
        # Повторный обмен кода — признак перехвата; код уже отработал.
        audit.warning(
            "token: code REUSE detected client=%s user_id=%s", client.client_id, row.user_id
        )
        raise OidcError("invalid_grant", "authorization code already used")
    if row.expires_at < _utcnow():
        raise OidcError("invalid_grant", "authorization code expired")
    if row.redirect_uri != (redirect_uri or ""):
        raise OidcError("invalid_grant", "redirect_uri mismatch")

    if row.code_challenge:
        if not code_verifier:
            raise OidcError("invalid_grant", "code_verifier required")
        if create_s256_code_challenge(code_verifier) != row.code_challenge:
            raise OidcError("invalid_grant", "PKCE verification failed")
    elif not client.is_confidential:
        raise OidcError("invalid_grant", "PKCE required for public client")

    user = await session.get(RadarUser, row.user_id)
    if user is None or not user.is_active:
        raise OidcError("invalid_grant", "user is not active")

    row.used_at = _utcnow()
    refresh_raw = await _new_refresh_token(
        session, user_id=user.id, client_id=client.client_id, scope=row.scope
    )
    await session.commit()

    audit.info(
        "token: code exchanged client=%s sub=%s scope=%s", client.client_id, user.sub, row.scope
    )
    return TokenBundle(
        access_token=_sign(_access_token_claims(user, client.client_id, row.scope)),
        id_token=_sign(
            _id_token_claims(
                user, client.client_id, row.scope, nonce=row.nonce, auth_time=row.auth_time
            )
        ),
        refresh_token=refresh_raw,
        expires_in=get_access_token_ttl(),
        scope=row.scope,
    )


async def refresh_grant(session, *, client: OAuthClient, raw_refresh: str) -> TokenBundle:
    """Grant ``refresh_token``: ротация + family reuse-detection (MUST §5.2)."""
    row: Optional[OAuthRefreshToken] = (
        await session.execute(
            select(OAuthRefreshToken).where(
                OAuthRefreshToken.token_hash == _sha256(raw_refresh or "")
            )
        )
    ).scalar_one_or_none()
    if row is None or row.client_id != client.client_id:
        raise OidcError("invalid_grant", "unknown refresh token")

    if row.revoked_at is not None:
        # Reuse погашенного токена → компрометация: гасим ВСЮ family.
        await session.execute(
            update(OAuthRefreshToken)
            .where(
                OAuthRefreshToken.family_id == row.family_id,
                OAuthRefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=_utcnow())
        )
        await session.commit()
        audit.warning(
            "token: refresh REUSE — family %s revoked (client=%s user_id=%s)",
            row.family_id,
            client.client_id,
            row.user_id,
        )
        raise OidcError("invalid_grant", "refresh token reuse detected; session revoked")

    if row.expires_at < _utcnow():
        raise OidcError("invalid_grant", "refresh token expired")

    user = await session.get(RadarUser, row.user_id)
    if user is None or not user.is_active:
        raise OidcError("invalid_grant", "user is not active")

    row.revoked_at = _utcnow()  # ротация: старый гасим, новый выдаём в той же family
    new_raw = await _new_refresh_token(
        session,
        user_id=user.id,
        client_id=client.client_id,
        scope=row.scope,
        family_id=row.family_id,
        rotated_from=row.id,
    )
    await session.commit()

    audit.info("token: refresh rotated client=%s sub=%s", client.client_id, user.sub)
    return TokenBundle(
        access_token=_sign(_access_token_claims(user, client.client_id, row.scope)),
        id_token=_sign(
            _id_token_claims(user, client.client_id, row.scope, nonce=None, auth_time=_utcnow())
        ),
        refresh_token=new_raw,
        expires_in=get_access_token_ttl(),
        scope=row.scope,
    )


# ---------------------------------------------------------------------------
# Userinfo
# ---------------------------------------------------------------------------


async def userinfo(session, bearer_token: str) -> Dict[str, Any]:
    """Claims по access-токену (RS256 JWT, aud=issuer)."""
    try:
        claims = jwt.decode(bearer_token, get_public_jwks())
        claims.validate(now=int(time.time()))
    except Exception as e:
        raise OidcError("invalid_token", f"access token invalid: {e}") from e
    if claims.get("iss") != get_issuer() or claims.get("aud") != get_issuer():
        raise OidcError("invalid_token", "wrong issuer/audience")

    user: Optional[RadarUser] = (
        await session.execute(select(RadarUser).where(RadarUser.sub == claims.get("sub")))
    ).scalar_one_or_none()
    if user is None or not user.is_active:
        raise OidcError("invalid_token", "user is not active")

    scopes: List[str] = str(claims.get("scope") or "").split()
    out: Dict[str, Any] = {"sub": user.sub}
    if "email" in scopes and user.email:
        out["email"] = user.email
        out["email_verified"] = bool(user.email_verified)
    if "profile" in scopes:
        out["name"] = user.display_name or user.login or ""
    return out


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def discovery_document() -> Dict[str, Any]:
    issuer = get_issuer()
    return {
        "issuer": issuer,
        "authorization_endpoint": f"{issuer}/oidc/authorize",
        "token_endpoint": f"{issuer}/oidc/token",
        "userinfo_endpoint": f"{issuer}/oidc/userinfo",
        "jwks_uri": f"{issuer}/.well-known/jwks.json",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "scopes_supported": list(SUPPORTED_SCOPES),
        "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": ["RS256"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": [
            "client_secret_post",
            "client_secret_basic",
            "none",
        ],
        "claims_supported": ["sub", "email", "email_verified", "name"],
    }
