"""Конфиг Радар-ID — OIDC-провайдера идентичности экосистемы (ADR-0002).

Радар-ID = модуль setka, выдающий identity (sub/email/email_verified/name)
клиентским сайтам экосистемы по OIDC Authorization Code + PKCE. Контракт
ратифицирован brain 2026-06-30.

Секреты — только в env (``/etc/setka/setka.env``, pool #008). Приватный
ключ подписи RS256 — файлом вне репо (потеря = пере-выпуск для всей
экосистемы; кандидат №1 на зеркало в Карман, ADR-0006 brain).

Env vars:
  RADAR_ID_ISSUER                # issuer OIDC (дефолт — punycode вход.вмалмыже.рф)
  RADAR_ID_PRIVATE_KEY_FILE      # путь к RS256 private key PEM
                                 # (дефолт /etc/setka/radar_id_rs256.pem)
  RADAR_ID_DISABLED=0            # аварийный kill-switch OIDC-поверхности
  RADAR_ID_ACCESS_TOKEN_TTL=600  # сек; короткие access (ADR-0002 §5.2)
  RADAR_ID_AUTH_CODE_TTL=60      # сек; одноразовый code
  RADAR_ID_REFRESH_TTL_DAYS=30   # дней жизни refresh-токена
"""

from __future__ import annotations

import os

# Issuer: канонический публичный домен вход.вмалмыже.рф (решение владельца
# 2026-06-30). В URL — punycode символ-в-символ (G108/R16): та же строка
# обязана стоять в конфиге каждого клиента и в ВК-приложении.
DEFAULT_ISSUER = "https://xn--b1ae3a1a.xn--80adkdyec4j.xn--p1ai"

DEFAULT_PRIVATE_KEY_FILE = "/etc/setka/radar_id_rs256.pem"

# Scopes, которые Радар-ID вообще умеет (суперсет; per-client потолок —
# oauth_clients.allowed_scopes).
SUPPORTED_SCOPES = ("openid", "profile", "email")


def get_issuer() -> str:
    return os.getenv("RADAR_ID_ISSUER", DEFAULT_ISSUER).rstrip("/")


def get_private_key_file() -> str:
    return os.getenv("RADAR_ID_PRIVATE_KEY_FILE", DEFAULT_PRIVATE_KEY_FILE)


def radar_id_disabled() -> bool:
    """Kill-switch OIDC-поверхности (дефолт — включена)."""
    return os.getenv("RADAR_ID_DISABLED", "0").strip().lower() in ("1", "true", "yes", "on")


def get_access_token_ttl() -> int:
    try:
        return int(os.getenv("RADAR_ID_ACCESS_TOKEN_TTL", "600"))
    except ValueError:
        return 600


def get_auth_code_ttl() -> int:
    try:
        return int(os.getenv("RADAR_ID_AUTH_CODE_TTL", "60"))
    except ValueError:
        return 60


def get_refresh_ttl_days() -> int:
    try:
        return int(os.getenv("RADAR_ID_REFRESH_TTL_DAYS", "30"))
    except ValueError:
        return 30
