"""
Runtime configuration (NO secrets in git).

This module replaces legacy direct imports from `config/config_secure.py` (secrets file is not part of the repo).
All values must come from environment (recommended: /etc/setka/setka.env loaded by systemd).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional
from urllib.parse import urlparse


def _getenv(name: str, default: Optional[str] = None) -> Optional[str]:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value


def _require(name: str) -> str:
    value = _getenv(name)
    if value is None:
        raise RuntimeError(f"{name} is required but not set")
    return value


def _load_json_env(name: str, default: Any) -> Any:
    raw = _getenv(name)
    if raw is None:
        return default
    try:
        return json.loads(raw)
    except Exception as e:
        raise RuntimeError(f"Invalid JSON in env var {name}: {e}") from e


def _collect_prefixed_tokens(prefix: str) -> Dict[str, str]:
    """
    Collect tokens from env vars like VK_TOKEN_VALSTAN=... => {"VALSTAN": "..."}.
    """
    out: Dict[str, str] = {}
    for k, v in os.environ.items():
        if not k.startswith(prefix):
            continue
        if not v:
            continue
        name = k[len(prefix) :].strip("_")
        if not name:
            continue
        out[name.upper()] = v
    return out


def _parse_redis_url(redis_url: str) -> Dict[str, Any]:
    u = urlparse(redis_url)
    if u.scheme not in ("redis", "rediss"):
        raise RuntimeError("REDIS_URL must start with redis:// or rediss://")
    db = 0
    if u.path and u.path != "/":
        try:
            db = int(u.path.lstrip("/"))
        except ValueError:
            db = 0
    return {
        "host": u.hostname or "localhost",
        "port": u.port or 6379,
        "db": db,
        "ssl": u.scheme == "rediss",
    }

def _parse_database_url(database_url: str) -> Dict[str, Any]:
    # Normalize scheme for urlparse
    parse_url = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    u = urlparse(parse_url)
    return {
        "user": u.username or "",
        "password": u.password or "",
        "host": u.hostname or "localhost",
        "port": u.port or 5432,
        "database": (u.path or "/").lstrip("/"),
    }


# Core URLs
DATABASE_URL = _require("DATABASE_URL")
REDIS_URL = _require("REDIS_URL")

# Back-compat dict used throughout legacy code
REDIS = _parse_redis_url(REDIS_URL)
POSTGRES = _parse_database_url(DATABASE_URL)

# AI
GROQ_API_KEY = _getenv("GROQ_API_KEY")

# Telegram
TELEGRAM_ALERT_CHAT_ID = _getenv("TELEGRAM_ALERT_CHAT_ID", "")
TELEGRAM_TOKENS: Dict[str, str] = {}
TELEGRAM_TOKENS.update(_collect_prefixed_tokens("TELEGRAM_TOKEN_"))
# Back-compat single-bot env var
single_alert_token = _getenv("TELEGRAM_ALERT_BOT_TOKEN")
if single_alert_token:
    TELEGRAM_TOKENS.setdefault("ALERT", single_alert_token)

# VK tokens
VK_TOKENS: Dict[str, str] = {}
VK_TOKENS.update(_collect_prefixed_tokens("VK_TOKEN_"))

# Optional: richer structures as JSON, if provided
VK_TOKENS_JSON = _load_json_env("VK_TOKENS_JSON", default=None)
if isinstance(VK_TOKENS_JSON, dict):
    for k, v in VK_TOKENS_JSON.items():
        if v:
            VK_TOKENS[str(k).upper()] = str(v)

# Some code expects MAIN/AUX split; map everything to MAIN by default
VK_MAIN_TOKENS = {name: {"token": token} for name, token in VK_TOKENS.items()}
VK_AUXILIARY_TOKENS: Dict[str, Dict[str, str]] = {}

# Workflow config
PRODUCTION_WORKFLOW_CONFIG = _load_json_env("PRODUCTION_WORKFLOW_CONFIG", default={})

# VK misc config (optional)
VK_TOKEN_CONFIG = _load_json_env("VK_TOKEN_CONFIG", default={})
VK_TEST_GROUP_ID = int(_getenv("VK_TEST_GROUP_ID", "0") or "0")
VK_PRODUCTION_GROUPS = _load_json_env("VK_PRODUCTION_GROUPS", default={})

# Optional legacy integrations
MONGO_CONNECTION = _getenv("MONGO_CONNECTION")

# Server info (optional)
SERVER = {
    "host": _getenv("SERVER_HOST", "127.0.0.1"),
    "port": int(_getenv("SERVER_PORT", "8000") or "8000"),
}


