"""
Runtime configuration (NO secrets in git).

This module replaces legacy direct imports from `config/config_secure.py`
(secrets file is not part of the repo). All values must come from environment
(recommended: /etc/setka/setka.env loaded by systemd).
"""

from __future__ import annotations

import ast
import json
import os
import re
from typing import Any, Dict, Optional, Set
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
        # Be forgiving in production: some deployments store python-literal dicts
        # in env vars (single quotes) instead of strict JSON. Try to parse those too.
        try:
            return ast.literal_eval(raw)
        except Exception:
            # Another real-world case: systemd EnvironmentFile strips quotes, turning JSON into:
            # {key:value,other_key:123} (keys and string values become unquoted).
            # Try a best-effort repair for simple dict-like payloads.
            try:
                repaired = raw.strip()
                if repaired.startswith("{") and repaired.endswith("}"):
                    # Quote keys: {foo:1,bar:baz} -> {"foo":1,"bar":baz}
                    repaired = re.sub(r"([{,])\s*([A-Za-z0-9_]+)\s*:", r'\1"\2":', repaired)

                    # Quote bareword string values (but keep numbers/bools/null unquoted).
                    def _quote_bare_value(m: re.Match[str]) -> str:
                        val = m.group(1)
                        tail = m.group(2)
                        low = val.lower()
                        if low in ("true", "false", "null"):
                            return f":{low}{tail}"
                        # number?
                        if re.fullmatch(r"-?\\d+(\\.\\d+)?", val):
                            return f":{val}{tail}"
                        return f':"{val}"{tail}'

                    repaired = re.sub(
                        r":\s*([A-Za-z_][A-Za-z0-9_\-]*)\s*([,}])",
                        _quote_bare_value,
                        repaired,
                    )
                    return json.loads(repaired)
            except Exception:
                pass

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

# ============================================================================
# VK Token Roles: PARSE vs PUBLISH (расширено 2026-05-27)
# ============================================================================
# Все ``VK_TOKENS`` могут читать VK (парсинг стен, поиск групп, getById).
# Публиковать (wall.post / wall.repost / wall.createComment / messages.send /
# photos.upload в качестве дайджеста или ответа модератора) могут только
# токены из явного whitelist ``VK_PUBLISH_TOKEN_NAMES``. Жёсткий deny-list
# ``VK_NEVER_PUBLISH_TOKEN_NAMES`` исключает имена даже если они попали в
# whitelist — это страховка от того что Vita когда-нибудь случайно появится
# среди публикаторов.
#
# Env vars:
#   VK_PUBLISH_TOKEN_NAMES="VALSTAN"            # CSV; кто может публиковать
#   VK_NEVER_PUBLISH_TOKEN_NAMES="VITA"         # CSV; кто никогда (override)
#   VK_PUBLISH_TOKEN_NAME="VALSTAN"             # legacy single; читается если
#                                                 # VK_PUBLISH_TOKEN_NAMES пусто
#
# Динамическое состояние (cooldown после VK error 5/17/29, ручной disable
# через UI) живёт в БД ``vk_tokens.disabled_until`` — см.
# ``modules.vk_token_router.TokenPolicy``. Здесь — только статический
# whitelist/deny-list, который НЕ читается из БД.

VK_PUBLISH_TOKEN_NAME = _getenv("VK_PUBLISH_TOKEN_NAME", "VALSTAN")


def _csv_token_names(raw: Optional[str]) -> list:
    """Parse CSV with token names; uppercase + strip + drop empties."""
    if not raw:
        return []
    return [item.strip().upper() for item in str(raw).split(",") if item and item.strip()]


def get_publish_token_names() -> list:
    """Список имён токенов, которым РАЗРЕШЕНО публиковать (whitelist).

    Сначала пробуем ``VK_PUBLISH_TOKEN_NAMES`` (CSV). Если не задан — fallback
    на старый single ``VK_PUBLISH_TOKEN_NAME``. Если и его нет — пустой
    список (то есть никто не может публиковать через user-token; только
    community-токены смогут).
    """
    names = _csv_token_names(_getenv("VK_PUBLISH_TOKEN_NAMES"))
    if names:
        return names
    if VK_PUBLISH_TOKEN_NAME:
        return [VK_PUBLISH_TOKEN_NAME.upper()]
    return []


def get_never_publish_token_names() -> set:
    """Имена токенов, которым НИКОГДА нельзя публиковать (hard deny).

    По умолчанию — ``{"VITA"}``. Override через env
    ``VK_NEVER_PUBLISH_TOKEN_NAMES`` (CSV). Override НЕ может убрать всех —
    пустая строка интерпретируется как «не задано» и применяется default.
    """
    names = _csv_token_names(_getenv("VK_NEVER_PUBLISH_TOKEN_NAMES"))
    if names:
        return set(names)
    return {"VITA"}


def get_publish_token() -> Optional[str]:
    """Legacy helper — первый из whitelist'а, который есть в VK_TOKENS.

    Сохраняется для обратной совместимости со старым кодом (VKPublisher,
    scripts/*). Новый код должен использовать
    :class:`modules.vk_token_router.TokenPolicy` для упорядоченного выбора
    кандидатов с учётом ``disabled_until``.
    """
    never = get_never_publish_token_names()
    for name in get_publish_token_names():
        if name in never:
            continue
        if name in VK_TOKENS:
            return VK_TOKENS[name]
    if VK_TOKENS:
        # Fallback: first available token, но всё ещё фильтруем never.
        for name, tok in VK_TOKENS.items():
            if name.upper() not in never and tok:
                return tok
    return None


def validate_publish_token(token: str, token_name: str = "") -> bool:
    """Может ли этот токен публиковать в принципе (env-only, без БД).

    Используется как защёлка перед wall.post — даже если что-то проскочило
    мимо TokenPolicy, мы не дадим Vita-токену уйти в публикацию.
    """
    never = get_never_publish_token_names()
    whitelist = set(get_publish_token_names())
    if token_name:
        upper = token_name.upper()
        if upper in never:
            return False
        if whitelist and upper not in whitelist:
            return False
    # Сверяем по содержимому токена: если он действительно один из
    # whitelist'а — пропускаем.
    for name, env_token in VK_TOKENS.items():
        if env_token == token:
            return name.upper() not in never and (not whitelist or name.upper() in whitelist)
    return False


def get_parse_tokens() -> Dict[str, str]:
    """Все VK-токены, пригодные для парсинга (READ).

    Парсинг разрешён всем active user-токенам, включая Vita. Динамический
    фильтр по ``disabled_until`` — на уровне TokenPolicy в async-контексте.
    """
    return dict(VK_TOKENS)


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
    "domain": _getenv("SERVER_DOMAIN", "3931b3fe50ab.vps.myjino.ru"),
}

# ---------------------------------------------------------------------------
# Сетевой хаб «copy / setka»: одна группа-источник → стены регионов (env-only)
# ---------------------------------------------------------------------------


def get_copy_setka_source_owner_id() -> int:
    """Группа-источник (отрицательный owner_id).

    По умолчанию — vk.com/copy_by_setka (-167381590)."""
    raw = _getenv("COPY_SETKA_SOURCE_GROUP_ID", "-167381590")
    if not raw or not str(raw).strip():
        return -167381590
    try:
        return int(str(raw).strip())
    except ValueError:
        return -167381590


def copy_setka_disabled() -> bool:
    """Полностью отключить сетевой хаб (например на стенде)."""
    return (_getenv("COPY_SETKA_DISABLED", "0") or "0").strip() in (
        "1",
        "true",
        "yes",
        "on",
    )


def copy_setka_use_repost() -> bool:
    """Устарело: режим теперь задаётся словом «репост» в тексте поста.

    Оставлено для совместимости."""
    v = (_getenv("COPY_SETKA_USE_REPOST", "1") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def get_copy_setka_max_post_age_hours() -> float:
    try:
        return float(_getenv("COPY_SETKA_MAX_POST_AGE_HOURS", "48") or "48")
    except ValueError:
        return 48.0


def get_copy_setka_repost_message() -> str:
    return _getenv("COPY_SETKA_REPOST_MESSAGE", "") or ""


def get_copy_setka_target_region_codes() -> Optional[Set[str]]:
    """Ограничить список регионов (коды через запятую); None = все активные."""
    raw = _getenv("COPY_SETKA_TARGET_REGION_CODES")
    if not raw or not str(raw).strip():
        return None
    return {x.strip().lower() for x in str(raw).split(",") if x.strip()}
