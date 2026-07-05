"""ВК-вход Радар-ID: VK ID OAuth 2.1 + PKCE (upstream-метод логина, R16).

Протокол id.vk.ru (НЕ старый oauth.vk.com), заземлён на рабочую реализацию
Sabantuy (brain REFERENCE R16):

- authorize ``https://id.vk.ru/authorize`` (code + PKCE S256 + state);
- token ``https://id.vk.ru/oauth2/auth`` — **client_secret НЕ участвует**
  (PKCE заменяет, даже для типа «Веб»); **``device_id`` из callback-query
  ОБЯЗАТЕЛЕН** в обмене (грабля R16 — нет в типовых гайдах);
- профиль ``https://id.vk.ru/oauth2/user_info``;
- ``redirect_uri`` символ-в-символ как зарегистрирован; ``.рф`` — punycode (G108).

Состояние между /login и /callback (state + code_verifier + next) — в
подписанной HMAC-cookie (тот же паттерн, что сессии radar: stateless,
SETKA_WEB_SECRET), TTL 10 минут.

Связывание аккаунтов (ADR-0002 §2): vk_user_id → существующий; иначе по
email ТОЛЬКО если у нашего аккаунта email_verified (анти-захват); иначе —
новый RadarUser (role=radar, sub авто). Email от VK считаем verified
(VK подтверждает его на своей стороне).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlencode

import httpx
from authlib.oauth2.rfc7636 import create_s256_code_challenge
from sqlalchemy import func, select

from database.models_extended import RadarUser

logger = logging.getLogger(__name__)
audit = logging.getLogger("radar_id.audit")

VK_AUTHORIZE_URL = "https://id.vk.ru/authorize"
VK_TOKEN_URL = "https://id.vk.ru/oauth2/auth"
VK_USERINFO_URL = "https://id.vk.ru/oauth2/user_info"

OAUTH_STATE_COOKIE = "radar_vk_oauth"
OAUTH_STATE_TTL = 600  # 10 минут на прохождение VK-диалога


class VkUpstreamError(RuntimeError):
    """Ошибка ВК-обмена (человекочитаемая — уходит на страницу логина)."""


def get_vk_app_id() -> str:
    """App ID VK ID-приложения Радара (env ``RADAR_ID_VK_APP_ID``; публичный)."""
    return os.getenv("RADAR_ID_VK_APP_ID", "").strip()


def get_redirect_uri() -> str:
    """redirect_uri ВК-callback — символ-в-символ как в ВК-приложении (punycode)."""
    from config.radar_id import get_issuer

    return os.getenv("RADAR_ID_VK_REDIRECT_URI", f"{get_issuer()}/auth/vk/callback")


def vk_login_available() -> bool:
    return bool(get_vk_app_id())


# ---------------------------------------------------------------------------
# Подписанный state-blob (cookie между /login и /callback)
# ---------------------------------------------------------------------------


def _secret() -> bytes:
    env = os.getenv("SETKA_WEB_SECRET")
    if env:
        return env.encode()
    # Тот же fallback, что у сессий radar: ephemeral деградация для dev.
    from modules.radar.auth import _secret as session_secret

    return session_secret()


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64d(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def sign_oauth_state(payload: Dict[str, Any], *, _now: Optional[float] = None) -> str:
    now = time.time() if _now is None else _now
    body = dict(payload)
    body["exp"] = int(now + OAUTH_STATE_TTL)
    body_b64 = _b64e(json.dumps(body, separators=(",", ":")).encode())
    sig = hmac.new(_secret(), body_b64.encode(), hashlib.sha256).digest()
    return f"{body_b64}.{_b64e(sig)}"


def verify_oauth_state(blob: str, *, _now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    try:
        body_b64, sig_b64 = blob.split(".")
        expected = hmac.new(_secret(), body_b64.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _b64d(sig_b64)):
            return None
        payload = json.loads(_b64d(body_b64))
        now = time.time() if _now is None else _now
        if int(payload.get("exp", 0)) < now:
            return None
        return payload
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


def safe_next(next_url: Optional[str]) -> str:
    """Разрешаем только внутренние относительные пути (анти open-redirect)."""
    if next_url and next_url.startswith("/") and not next_url.startswith("//"):
        return next_url
    return "/radar"


# ---------------------------------------------------------------------------
# OAuth-шаги
# ---------------------------------------------------------------------------


def build_vk_authorize(next_url: str) -> Tuple[str, str]:
    """Собрать VK authorize-URL. Возвращает ``(url, signed_state_blob)``."""
    if not vk_login_available():
        raise VkUpstreamError("ВК-вход не настроен (RADAR_ID_VK_APP_ID пуст)")
    state = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(48)
    params = {
        "response_type": "code",
        "client_id": get_vk_app_id(),
        "redirect_uri": get_redirect_uri(),
        "state": state,
        "code_challenge": create_s256_code_challenge(verifier),
        "code_challenge_method": "S256",
        "scope": "email",
    }
    url = f"{VK_AUTHORIZE_URL}?{urlencode(params)}"
    blob = sign_oauth_state({"st": state, "cv": verifier, "next": safe_next(next_url)})
    return url, blob


async def exchange_vk_code(
    *,
    code: str,
    device_id: str,
    state: str,
    code_verifier: str,
    client: Optional[httpx.AsyncClient] = None,
) -> Dict[str, Any]:
    """Обменять code на access_token VK (device_id обязателен — R16)."""
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "code_verifier": code_verifier,
        "client_id": get_vk_app_id(),
        "device_id": device_id,
        "redirect_uri": get_redirect_uri(),
        "state": state,
    }
    own = client is None
    client = client or httpx.AsyncClient(timeout=15)
    try:
        resp = await client.post(VK_TOKEN_URL, data=data)
        body = resp.json()
    except Exception as e:
        raise VkUpstreamError(f"VK token endpoint недоступен: {e}") from e
    finally:
        if own:
            await client.aclose()
    if resp.status_code != 200 or "access_token" not in body:
        err = body.get("error_description") or body.get("error") or f"HTTP {resp.status_code}"
        raise VkUpstreamError(f"VK не принял обмен кода: {err}")
    return body


async def fetch_vk_user(
    access_token: str, *, client: Optional[httpx.AsyncClient] = None
) -> Dict[str, Any]:
    """Профиль по access_token: ``{user_id, first_name, last_name, email?}``."""
    own = client is None
    client = client or httpx.AsyncClient(timeout=15)
    try:
        resp = await client.post(
            VK_USERINFO_URL,
            data={"access_token": access_token, "client_id": get_vk_app_id()},
        )
        body = resp.json()
    except Exception as e:
        raise VkUpstreamError(f"VK user_info недоступен: {e}") from e
    finally:
        if own:
            await client.aclose()
    user = (body or {}).get("user") or {}
    if resp.status_code != 200 or not user.get("user_id"):
        raise VkUpstreamError("VK не вернул профиль пользователя")
    return user


# ---------------------------------------------------------------------------
# Связывание с RadarUser
# ---------------------------------------------------------------------------


async def find_or_create_user(session, vk_user: Dict[str, Any]) -> RadarUser:
    """vk_user_id → существующий; по verified-email → привязка; иначе новый."""
    vk_id = int(vk_user["user_id"])
    email = (vk_user.get("email") or "").strip() or None
    name = (
        " ".join(p for p in (vk_user.get("first_name"), vk_user.get("last_name")) if p).strip()
        or None
    )

    row: Optional[RadarUser] = (
        await session.execute(select(RadarUser).where(RadarUser.vk_user_id == vk_id))
    ).scalar_one_or_none()
    if row is not None:
        if not row.is_active:
            raise VkUpstreamError("Аккаунт деактивирован")
        return row

    if email:
        by_email: Optional[RadarUser] = (
            await session.execute(
                select(RadarUser).where(func.lower(RadarUser.email) == email.lower())
            )
        ).scalar_one_or_none()
        # Привязка соц-личности к существующему аккаунту — только через
        # verified email (ADR-0002 §2: иначе захват аккаунта по чужому email).
        if by_email is not None and by_email.email_verified and by_email.is_active:
            by_email.vk_user_id = vk_id
            if name and not by_email.display_name:
                by_email.display_name = name
            await session.commit()
            audit.info("vk-login: linked vk_id=%s to sub=%s via email", vk_id, by_email.sub)
            return by_email

    user = RadarUser(
        login=None,
        password_hash=None,
        role="radar",
        vk_user_id=vk_id,
        email=email,
        email_verified=bool(email),  # email пришёл от VK — VK его подтверждал
        display_name=name,
    )
    session.add(user)
    await session.commit()
    audit.info("vk-login: created RadarUser sub=%s for vk_id=%s", user.sub, vk_id)
    return user
