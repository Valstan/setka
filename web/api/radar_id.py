"""HTTP-слой Радар-ID: OIDC-эндпоинты (ADR-0002 Ф1).

Маршруты (абсолютные, без префикса — discovery требует канонических путей):
- ``GET /.well-known/openid-configuration`` — discovery (public);
- ``GET /.well-known/jwks.json`` — публичные ключи (public, офлайн-валидация);
- ``GET /oidc/authorize`` — вход в code-flow; требует сессию RadarUser
  (AuthGate отправит на /login?next=... с сохранением query);
- ``POST /oidc/token`` — обмен кода / refresh (public, своя client-auth);
- ``GET /oidc/userinfo`` — claims по Bearer access-токену (public).

Consent: клиенты Радар-ID регистрируются вручную оператором и все —
first-party экосистемы (ADR-0002 §8), поэтому согласие неявное
(auto-approve) — отдельного consent-экрана в Ф1 нет. При появлении
сторонних клиентов consent-страница добавляется перед issue_auth_code.

Rate-limit (MUST §5.3): fixed-window per-IP через Redis (переиспользуем
GatewayQuota; Redis недоступен → fail-open, приоритет доступности входа).
Audit — logger ``radar_id.audit`` (json-логи systemd).
"""

from __future__ import annotations

import base64
import logging
from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Header, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from config.radar_id import radar_id_disabled
from database.connection import AsyncSessionLocal
from modules.radar_id import service
from modules.radar_id.keys import get_public_jwks
from modules.radar_id.service import OidcError

logger = logging.getLogger(__name__)

router = APIRouter()

# Щедрые лимиты для login-трафика: люди, не боты. Разделяем окна authorize
# (браузерные редиректы) и token (серверные обмены).
RATE_PER_MIN = 30
RATE_PER_DAY = 2000


def _check_enabled() -> None:
    if radar_id_disabled():
        raise HTTPException(status_code=503, detail="radar-id disabled")


def _enforce_ip_rate(request: Request, bucket: str) -> None:
    from modules.gateway.quota import GatewayQuota
    from modules.vk_monitor.rate_limiter import _build_redis_client

    ip = request.client.host if request.client else "unknown"
    quota = GatewayQuota(_build_redis_client(), RATE_PER_MIN, RATE_PER_DAY)
    allowed, retry_after = quota.check_and_consume(f"oidc:{bucket}:{ip}")
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Too many requests",
            headers={"Retry-After": str(retry_after)},
        )


# ---------------------------------------------------------------------------
# Discovery / JWKS
# ---------------------------------------------------------------------------


@router.get("/.well-known/openid-configuration")
async def openid_configuration():
    _check_enabled()
    return service.discovery_document()


@router.get("/.well-known/jwks.json")
async def jwks():
    _check_enabled()
    try:
        return get_public_jwks()
    except Exception as e:
        # Ключ не настроен — громко, но без утечки путей.
        logger.error("radar-id: jwks unavailable: %s", e)
        raise HTTPException(status_code=503, detail="signing key unavailable")


# ---------------------------------------------------------------------------
# Authorize
# ---------------------------------------------------------------------------


def _error_redirect(redirect_uri: str, error: str, description: str, state: Optional[str]):
    params = {"error": error}
    if description:
        params["error_description"] = description
    if state:
        params["state"] = state
    sep = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(f"{redirect_uri}{sep}{urlencode(params)}", status_code=302)


@router.get("/oidc/authorize")
async def authorize(
    request: Request,
    response_type: str = "",
    client_id: str = "",
    redirect_uri: str = "",
    scope: str = "",
    state: Optional[str] = None,
    nonce: Optional[str] = None,
    code_challenge: Optional[str] = None,
    code_challenge_method: Optional[str] = None,
):
    _check_enabled()
    _enforce_ip_rate(request, "authorize")

    user = getattr(request.state, "user", None)
    if user is None:
        # AuthGate обязан был аутентифицировать (маршрут не в PUBLIC).
        raise HTTPException(status_code=401, detail="Not authenticated")

    async with AsyncSessionLocal() as session:
        client = await service.get_client(session, client_id)
        # Ошибки клиента/redirect_uri НИКОГДА не редиректим на непроверенный
        # uri — только 400 (иначе open-redirect).
        if client is None:
            raise HTTPException(status_code=400, detail="unknown client_id")
        try:
            service.validate_redirect_uri(client, redirect_uri)
        except OidcError as e:
            raise HTTPException(status_code=400, detail=e.description)

        if response_type != "code":
            return _error_redirect(
                redirect_uri, "unsupported_response_type", "only code is supported", state
            )
        try:
            granted = service.resolve_scope(client, scope)
            raw_code = await service.issue_auth_code(
                session,
                client=client,
                user=user,
                redirect_uri=redirect_uri,
                scope=granted,
                code_challenge=code_challenge,
                code_challenge_method=code_challenge_method,
                nonce=nonce,
            )
        except OidcError as e:
            return _error_redirect(redirect_uri, e.error, e.description, state)

    params = {"code": raw_code}
    if state:
        params["state"] = state
    sep = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(f"{redirect_uri}{sep}{urlencode(params)}", status_code=302)


# ---------------------------------------------------------------------------
# Token
# ---------------------------------------------------------------------------


def _client_creds_from_basic(authorization: Optional[str]):
    """client_secret_basic: Authorization: Basic b64(client_id:secret)."""
    if not authorization or not authorization.lower().startswith("basic "):
        return None, None
    try:
        raw = base64.b64decode(authorization.split(" ", 1)[1]).decode()
        cid, _, secret = raw.partition(":")
        return cid or None, secret or None
    except Exception:
        return None, None


@router.post("/oidc/token")
async def token(
    request: Request,
    grant_type: str = Form(""),
    code: str = Form(""),
    redirect_uri: str = Form(""),
    code_verifier: Optional[str] = Form(None),
    refresh_token: str = Form(""),
    client_id: str = Form(""),
    client_secret: Optional[str] = Form(None),
    authorization: Optional[str] = Header(None),
):
    _check_enabled()
    _enforce_ip_rate(request, "token")

    basic_id, basic_secret = _client_creds_from_basic(authorization)
    cid = basic_id or client_id
    secret = basic_secret if basic_id else client_secret

    async with AsyncSessionLocal() as session:
        try:
            client = await service.authenticate_client(session, cid, secret)
            if grant_type == "authorization_code":
                bundle = await service.exchange_code(
                    session,
                    client=client,
                    raw_code=code,
                    redirect_uri=redirect_uri,
                    code_verifier=code_verifier,
                )
            elif grant_type == "refresh_token":
                bundle = await service.refresh_grant(
                    session, client=client, raw_refresh=refresh_token
                )
            else:
                raise OidcError("unsupported_grant_type", f"grant_type={grant_type!r}")
        except OidcError as e:
            status = 401 if e.error == "invalid_client" else 400
            return JSONResponse(
                {"error": e.error, "error_description": e.description},
                status_code=status,
                headers={"Cache-Control": "no-store"},
            )

    return JSONResponse(bundle.as_response(), headers={"Cache-Control": "no-store"})


# ---------------------------------------------------------------------------
# Userinfo
# ---------------------------------------------------------------------------


@router.get("/oidc/userinfo")
async def userinfo(authorization: Optional[str] = Header(None)):
    _check_enabled()
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=401,
            detail="Bearer token required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    bearer = authorization.split(" ", 1)[1].strip()
    async with AsyncSessionLocal() as session:
        try:
            return await service.userinfo(session, bearer)
        except OidcError as e:
            raise HTTPException(
                status_code=401,
                detail=e.description,
                headers={"WWW-Authenticate": f'Bearer error="{e.error}"'},
            )
