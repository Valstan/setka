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
# photos.upload в качестве сводки или ответа модератора) могут только
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


def get_copy_setka_post_interval_seconds() -> float:
    """Пауза между репостами по регионам внутри одного прогона (анти-Captcha).

    16+ быстрых wall.repost с одного аккаунта подряд → VK требует капчу на
    хвостовых регионах. Разносим публикации во времени. Дефолт 5с (16 целей
    ≈80с, в пределах expires=1800 у beat-таски). 0 = без паузы (тесты)."""
    try:
        return max(0.0, float(_getenv("COPY_SETKA_POST_INTERVAL_SECONDS", "5") or "5"))
    except ValueError:
        return 5.0


# --- Поток «Кругозор»: научпоп/познавательное → веером на стены регионов -------
# (решение владельца 2026-06-14: новости науки + оптимистичное «Время-Вперёд»
# для расширения кругозора, разносол между местными новостями). Источники —
# сообщества category='krugozor' (SciTopus, НауЧпок, Batrachospermum, Время-Вперёд),
# уцелели в БД от Постопуса. Веер переиспользует кубики copy_setka, но отдельным
# модулем (мульти-источник + ротация + копи-режим). OFF по умолчанию (#008).


def krugozor_broadcast_disabled() -> bool:
    """Поток «Кругозор» выключен (дефолт — ВЫКЛ, гейт владельца #008).

    ON => beat-таска раз в день берёт свежий пост следующего по ротации
    krugozor-источника и копирует его в нативный пост на стены целевых регионов
    (с native-атрибуцией VK). Пока env не выставлен — рассылка не идёт."""
    return (_getenv("KRUGOZOR_BROADCAST_DISABLED", "1") or "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def get_krugozor_source_category() -> str:
    """Категория сообществ-источников научпопа (по умолчанию 'krugozor')."""
    return (_getenv("KRUGOZOR_SOURCE_CATEGORY", "krugozor") or "krugozor").strip() or "krugozor"


def get_krugozor_max_post_age_hours() -> float:
    """Не рассылать посты старше N часов (свежесть научпопа). Дефолт 72ч."""
    try:
        return float(_getenv("KRUGOZOR_MAX_POST_AGE_HOURS", "72") or "72")
    except ValueError:
        return 72.0


def get_krugozor_post_interval_seconds() -> float:
    """Пауза между публикациями по регионам (анти-Captcha, как у copy_setka)."""
    try:
        return max(0.0, float(_getenv("KRUGOZOR_POST_INTERVAL_SECONDS", "5") or "5"))
    except ValueError:
        return 5.0


# --- Сетевая рассылка: внутренний планировщик-публикатор -----------------------
# (директива brain 2026-06-14). Оператор собирает кампанию в SARAFAN и
# планирует её; диспетчер-беат публикует wall.post немедленно в заданное время,
# повтор N раз. В отличие от krugozor — НЕ авто-поток, а ручные кампании; гейт
# не «off по умолчанию» (кампании не существуют, пока их не создали), но есть
# аварийный kill-switch BROADCAST_DISABLED.


def broadcast_disabled() -> bool:
    """Аварийный стоп диспетчера рассылки (дефолт — ВКЛючён, т.е. работает).

    BROADCAST_DISABLED=1/true/yes/on => диспетчер не публикует ничего (no-op),
    даже если есть запланированные кампании. Безопасность по умолчанию держится
    не этим флагом, а тем, что кампания должна быть явно создана и запланирована
    оператором."""
    return (_getenv("BROADCAST_DISABLED", "0") or "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def radar_delivery_disabled() -> bool:
    """Аварийный стоп доставки радара во внешние выводы (дефолт — ВКЛючена).

    RADAR_DELIVERY_DISABLED=1/true/yes/on => хук доставки в TG/VK-выводы no-op
    (лента/архив/push не затронуты). Безопасность по умолчанию держится тем, что
    внешний вывод должен быть явно создан юзером в кабинете (off, пока не задан)."""
    return (_getenv("RADAR_DELIVERY_DISABLED", "0") or "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def get_broadcast_post_interval_seconds() -> float:
    """Пауза между публикациями по целям (анти-Captcha). Дефолт 5с.

    Probe на живых данных krugozor: 16 wall.post @5с = 16/16 без капчи; бурст
    @3с ловит капчу. 0 = без паузы (тесты)."""
    try:
        return max(0.0, float(_getenv("BROADCAST_POST_INTERVAL_SECONDS", "5") or "5"))
    except ValueError:
        return 5.0


def get_broadcast_default_repeat_interval_hours() -> float:
    """Дефолтный интервал между повторами кампании (часы), если не задан. 24ч."""
    try:
        return max(0.0, float(_getenv("BROADCAST_DEFAULT_REPEAT_INTERVAL_HOURS", "24") or "24"))
    except ValueError:
        return 24.0


def get_krugozor_target_region_codes() -> Optional[Set[str]]:
    """Ограничить регионы-цели (коды через запятую); None = все активные.

    Для smoke перед всей областью выставляется в 1 район, затем снимается."""
    raw = _getenv("KRUGOZOR_TARGET_REGION_CODES")
    if not raw or not str(raw).strip():
        return None
    return {x.strip().lower() for x in str(raw).split(",") if x.strip()}


def get_krugozor_source_exclude_ids() -> Set[int]:
    """Исключить отдельные источники по vk_id (отрицательные), через запятую.

    Напр. убрать «Время-Вперёд» из ротации, не трогая category в БД."""
    raw = _getenv("KRUGOZOR_SOURCE_EXCLUDE_IDS")
    if not raw or not str(raw).strip():
        return set()
    out: Set[int] = set()
    for x in str(raw).split(","):
        x = x.strip()
        if not x:
            continue
        try:
            out.add(int(x))
        except ValueError:
            continue
    return out


def get_krugozor_bulletin_max_items() -> int:
    """Сколько новостей max в одном научпоп-сводкае (решение владельца: до 4)."""
    try:
        return max(1, int(_getenv("KRUGOZOR_BULLETIN_MAX_ITEMS", "4") or "4"))
    except ValueError:
        return 4


def get_krugozor_snippet_len() -> int:
    """До скольких знаков укорачивать длинный пост-источник в сводке (анонс)."""
    try:
        return max(120, int(_getenv("KRUGOZOR_SNIPPET_LEN", "500") or "500"))
    except ValueError:
        return 500


def get_krugozor_text_budget() -> int:
    """Бюджет длины всей сводки («сколько влезёт») — добираем пункты до него."""
    try:
        return max(800, int(_getenv("KRUGOZOR_TEXT_BUDGET", "3500") or "3500"))
    except ValueError:
        return 3500


def krugozor_bulletin_photos_enabled() -> bool:
    """Прикладывать лид-фото каждого пункта (грид). Дефолт — да (решение владельца)."""
    return (_getenv("KRUGOZOR_BULLETIN_PHOTOS", "1") or "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def get_radar_bot_name() -> str:
    """Имя бота-приёмника каналов радара (ключ в TELEGRAM_TOKENS). Пусто = выкл (#008).

    Владелец заводит бота «Карман» у @BotFather → env TELEGRAM_TOKEN_KARMAN=<token>
    + RADAR_BOT_NAME=KARMAN. Пока не задано — приёмник не поллится."""
    return (_getenv("RADAR_BOT_NAME", "") or "").strip().upper()


def get_radar_vk_community_id() -> Optional[int]:
    """ID VK-сообщества «точки радара» для лички (Bots Long Poll). 0/пусто = выкл (#008).

    Владелец включает в админке сообщества Сообщения + Long Poll API (событие
    message_new), затем env RADAR_VK_COMMUNITY_ID=<id> (положительный). Community-токен
    берётся из БД-роутинга (load_vk_routing). Пока не задано — VK-интейк не поллится."""
    raw = (_getenv("RADAR_VK_COMMUNITY_ID", "") or "").strip()
    try:
        gid = abs(int(raw))
        return gid or None
    except (TypeError, ValueError):
        return None


def get_radar_bot_allowed_users() -> Set[int]:
    """Telegram user-id, которым бот разрешает добавлять каналы (через запятую).

    Пусто = никому (защита от чужих). Неавторизованному бот ответит его id, чтобы
    владелец узнал свой и внёс сюда."""
    raw = _getenv("RADAR_BOT_ALLOWED_USERS", "")
    out: Set[int] = set()
    for part in (raw or "").replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.add(int(part))
        except ValueError:
            continue
    return out


def get_radar_bot_radar_user_id() -> int:
    """radar_users.id, кого подписывать на добавленные через бота каналы (дефолт 1 —
    оператор valstan)."""
    try:
        return int(_getenv("RADAR_BOT_RADAR_USER_ID", "1") or "1")
    except ValueError:
        return 1


def krugozor_promo_filter_enabled() -> bool:
    """Пропускать рекламные/промо-посты источника (не лить их в районные паблики).

    Дефолт — да (решение владельца 2026-06-14). Высокоточный фильтр: только
    официальная VK-метка `marked_as_ads` + легальные маркеры (erid:/#реклама/
    «на правах рекламы»), без commercial-scoring (тот ложно бьёт по научному тексту)."""
    return (_getenv("KRUGOZOR_PROMO_FILTER", "1") or "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


# --- LLM-курация сводок (shadow PoC, письмо brain 2026-06-07) -------------


def bulletin_curation_shadow_enabled() -> bool:
    """Включить shadow-журнал LLM-курации сводок (Фаза 1).

    OFF по умолчанию — нулевая регрессия. ON => после публикации сводки его
    посты паркуются в `bulletin_curation_runs` для пост-фактум LLM-вердикта
    (/curate). Публикация при этом НЕ затрагивается (shadow)."""
    return (_getenv("BULLETIN_CURATION_SHADOW_ENABLED", "0") or "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def get_bulletin_curation_region_codes() -> Optional[Set[str]]:
    """Ограничить shadow-курацию списком кодов регионов (через запятую).

    None / пусто = все регионы (но для PoC brain рекомендует 1 регион, см.
    письмо 2026-06-07 — задаём явный allowlist в `/etc/setka/setka.env`)."""
    raw = _getenv("BULLETIN_CURATION_REGION_CODES")
    if not raw or not str(raw).strip():
        return None
    return {x.strip().lower() for x in str(raw).split(",") if x.strip()}


# --- Near-dup дедуп сводок (SimHash + Jaccard), env-тюнинг -----------------
# Параметры вынесены в env, чтобы калибровать порог на проде без передеплоя.
# Дефолты совпадают с прежними хардкодами → нулевая регрессия (кроме Jaccard,
# который новый и включён консервативно).


def get_bulletin_similarity_threshold() -> float:
    """Порог near-dup по SimHash (доля схожести). Дефолт 0.90 (как было)."""
    try:
        v = float(_getenv("BULLETIN_SIMILARITY_THRESHOLD", "0.90") or "0.90")
    except ValueError:
        return 0.90
    return min(1.0, max(0.0, v))


def get_bulletin_simhash_bucket_gate() -> int:
    """Насколько соседние длины-корзины сравнивать (|Δbucket| ≤ gate). Дефолт 1."""
    try:
        return max(0, int(_getenv("BULLETIN_SIMHASH_BUCKET_GATE", "1") or "1"))
    except ValueError:
        return 1


def bulletin_jaccard_dedup_enabled() -> bool:
    """Включить intra-batch Jaccard near-dup (ловит переставленные/переписанные
    дубли в пределах одной сводки). ON по умолчанию, консервативный порог;
    мгновенно отключается env при ложных срабатываниях."""
    return (_getenv("BULLETIN_JACCARD_DEDUP_ENABLED", "1") or "1").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def get_bulletin_jaccard_threshold() -> float:
    """Порог Jaccard по множеству слов (|A∩B|/|A∪B|). Дефолт 0.85 (консервативно —
    переписанный пересказ той же новости делит ~0.7–0.9 слов, разные ~0.2–0.4)."""
    try:
        v = float(_getenv("BULLETIN_JACCARD_THRESHOLD", "0.85") or "0.85")
    except ValueError:
        return 0.85
    return min(1.0, max(0.0, v))


def get_bulletin_jaccard_min_tokens() -> int:
    """Минимум слов в множестве, чтобы вообще применять Jaccard (короткие тексты
    дают ложные совпадения по шаблонным словам). Дефолт 10."""
    try:
        return max(1, int(_getenv("BULLETIN_JACCARD_MIN_TOKENS", "10") or "10"))
    except ValueError:
        return 10
