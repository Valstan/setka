"""Ядро self-serve выдачи: клиент ЕСА (OIDC) и ключ VK-шлюза (ADR-0010).

Одна процедура на оба канала — так требует мандат brain 2026-07-28: «не два
разных механизма». Здесь живут валидация и запись в БД; вызывателей два:

* HTTP — :mod:`web.api.ecosystem` (``/api/ecosystem/*``), аутентификация
  экосистемным ключом; так подключается проект-клиент **сам**;
* CLI — ``scripts/register_oidc_client.py`` и ``scripts/issue_gateway_key.py``
  на хосте под root; остаются для случаев, когда self-serve не годится
  (правка чужого клиента, аварийная ротация, импорт env-ключей).

**Почему у HTTP правила строже, чем у CLI.** Экосистемный ключ один на всех
клиентов, поэтому «обновить существующую запись» по HTTP — это возможность
одного проекта перехватить чужого клиента (перевесить ``redirect_uri`` на
себя) или перевыпустить чужой ключ шлюза. Отсюда правило: **создать новое —
можно, тронуть существующее — только предъявив его текущий секрет**. Иначе
409, и дальше либо владелец записи, либо оператор через CLI. У CLI такого
ограничения нет: там root на хосте, он и так может всё.

Секрет показывается ОДИН раз, и в БД его нет ни у одного из каналов:
у OIDC-клиента лежит scrypt-hash пароля, у ключа шлюза — SHA-256
(миграции 074/075, мандат brain 2026-08-01; почему разные алгоритмы —
в докстринге ``modules/gateway/keys.py``). Потерянный секрет не
восстанавливается, а ротируется.
"""

from __future__ import annotations

import hmac
import logging
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional
from urllib.parse import urlsplit

from sqlalchemy import select

from config.radar_id import SUPPORTED_SCOPES

logger = logging.getLogger(__name__)

# Ключи брендинга, которые понимает страница входа (modules/radar_id/branding.py).
BRAND_KEYS = ("title", "icon", "accent", "sub")

CLIENT_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,62}$")
PROJECT_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,49}$")
ACCENT_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

MAX_REDIRECT_URIS = 5
MAX_URI_LENGTH = 512
# http допускаем только для локальной отладки клиента — наружу такой uri
# бессмыслен, а запретить совсем нельзя: dev-callback нужен при интеграции.
LOCAL_HOSTS = ("localhost", "127.0.0.1", "[::1]")


class ProvisioningError(Exception):
    """Отказ выдачи с машинно-читаемым кодом.

    ``code`` уходит клиенту как есть (он программный потребитель, а не
    человек), ``message`` — человеку в лог/ответ. ``status`` — HTTP-код для
    web-слоя; CLI его игнорирует.
    """

    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclass
class ProvisionResult:
    """Результат выдачи. ``secret`` заполнен только когда он новый."""

    action: str  # created | updated | rotated | unchanged
    identifier: str  # client_id или имя проекта
    secret: Optional[str] = None
    details: Dict[str, object] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Валидация (чистые функции — покрыты юнит-тестами без БД)
# ---------------------------------------------------------------------------
def validate_client_id(value: str) -> str:
    """``client_id`` — короткий латинский slug; он же попадает в логи аудита."""
    value = (value or "").strip()
    if not CLIENT_ID_RE.match(value):
        raise ProvisioningError(
            "invalid_client_id",
            "client_id: латиница в нижнем регистре, цифры, '-' и '_', "
            "2–63 символа, начинается с буквы",
        )
    return value


def validate_name(value: str) -> str:
    value = (value or "").strip()
    if not 1 <= len(value) <= 128:
        raise ProvisioningError("invalid_name", "name: от 1 до 128 символов")
    return value


def validate_redirect_uris(uris: Optional[List[str]]) -> List[str]:
    """Точные redirect_uri: https (или http на localhost), ASCII, без фрагмента.

    Не-ASCII хост отбивается с прямой подсказкой про punycode: кириллический
    ``.рф`` в OAuth-полях сравнивается символ-в-символ, и клиент, приславший
    ``вмалмыже.рф``, получил бы ``invalid_redirect_uri`` уже в бою (G108).
    """
    if not uris:
        raise ProvisioningError("invalid_redirect_uri", "нужен хотя бы один redirect_uri")
    if len(uris) > MAX_REDIRECT_URIS:
        raise ProvisioningError(
            "invalid_redirect_uri", f"не больше {MAX_REDIRECT_URIS} redirect_uri на клиента"
        )
    out: List[str] = []
    for raw in uris:
        uri = (raw or "").strip()
        if not uri or len(uri) > MAX_URI_LENGTH:
            raise ProvisioningError(
                "invalid_redirect_uri", f"redirect_uri пустой или длиннее {MAX_URI_LENGTH} символов"
            )
        parts = urlsplit(uri)
        if parts.fragment:
            raise ProvisioningError(
                "invalid_redirect_uri", f"redirect_uri не должен содержать фрагмент: {uri}"
            )
        if not parts.netloc:
            raise ProvisioningError(
                "invalid_redirect_uri", f"redirect_uri должен быть абсолютным: {uri}"
            )
        try:
            uri.encode("ascii")
        except UnicodeEncodeError:
            raise ProvisioningError(
                "invalid_redirect_uri",
                f"redirect_uri обязан быть в punycode (ASCII), не кириллицей: {uri}",
            )
        host = parts.hostname or ""
        if parts.scheme == "https":
            pass
        elif parts.scheme == "http" and host in LOCAL_HOSTS:
            pass
        else:
            raise ProvisioningError(
                "invalid_redirect_uri",
                f"redirect_uri: только https (http допустим лишь на localhost): {uri}",
            )
        if uri in out:
            continue
        out.append(uri)
    return out


def validate_scopes(scopes: Optional[str]) -> str:
    """Потолок scope клиента: подмножество поддерживаемых, обязательно openid."""
    requested = [s for s in (scopes or "").split() if s]
    if not requested:
        requested = ["openid", "profile"]
    unknown = [s for s in requested if s not in SUPPORTED_SCOPES]
    if unknown:
        raise ProvisioningError(
            "invalid_scope",
            f"неизвестные scope: {' '.join(unknown)}; поддерживаются: {' '.join(SUPPORTED_SCOPES)}",
        )
    if "openid" not in requested:
        raise ProvisioningError("invalid_scope", "scope обязан включать openid")
    # Канонический порядок — детерминизм в логах и тестах.
    return " ".join(s for s in SUPPORTED_SCOPES if s in set(requested))


def validate_branding(branding: Optional[Dict[str, str]]) -> Dict[str, str]:
    """Карточка сервиса на странице входа. Пусто → ``{}`` («не трогай»).

    Цвет валидируем строго: дальше обработка fail-open
    (``modules/radar_id/branding.py``), и кривое значение не упадёт, а тихо
    отрисуется дефолтным — заметит это уже посетитель.
    """
    if not branding:
        return {}
    unknown = [k for k in branding if k not in BRAND_KEYS]
    if unknown:
        raise ProvisioningError(
            "invalid_branding",
            f"неизвестные ключи брендинга: {' '.join(sorted(unknown))}; "
            f"допустимы: {' '.join(BRAND_KEYS)}",
        )
    out: Dict[str, str] = {}
    for key, value in branding.items():
        if value is None:
            continue
        value = str(value).strip()
        if key == "accent" and not ACCENT_RE.match(value):
            raise ProvisioningError(
                "invalid_branding",
                f"branding.accent: ожидался цвет вида #1f7a4d, получено {value!r}",
            )
        if key == "icon" and len(value) > 8:
            raise ProvisioningError("invalid_branding", "branding.icon: не длиннее 8 символов")
        if key in ("title", "sub") and len(value) > 128:
            raise ProvisioningError("invalid_branding", f"branding.{key}: не длиннее 128 символов")
        out[key] = value
    return out


def merge_branding(existing: Optional[dict], given: Dict[str, str]) -> Optional[dict]:
    """Слить переданный брендинг с тем, что уже лежит в БД.

    Ничего не передали → ``None``-запрос «не трогай»: возвращаем ``existing``
    как есть (в т.ч. ``None``). Передали часть → правим только её, остальные
    ключи карточки переживают частичное обновление.
    """
    if not given:
        return existing
    merged = dict(existing or {})
    merged.update(given)
    return merged


def validate_project_name(value: str) -> str:
    """Имя потребителя шлюза: ``KAZANSKAYA``, ``GONBA`` — как ``GATEWAY_KEY_<P>``."""
    value = (value or "").strip().upper()
    if not PROJECT_NAME_RE.match(value):
        raise ProvisioningError(
            "invalid_project",
            "project: латиница в верхнем регистре, цифры и '_', 2–50 символов, "
            "начинается с буквы",
        )
    return value


MAX_BINDING_TARGETS = 50  # у реальных потребителей 1–6 целей; 50 — щедрый потолок


def _validate_gateway_binding(
    owner_ids: Optional[List[int]], screen_names: Optional[List[str]]
) -> tuple:
    """Нормализовать привязку D-047: (list[int], list[str]) либо ProvisioningError.

    Валидация — та же, что у enforcement (:mod:`modules.gateway.scope`), чтобы
    записанное здесь гарантированно читалось там: один словарь на обе стороны.
    """
    from modules.gateway.scope import KeyBinding, ScopeRefused, is_valid_screen_name

    try:
        binding = KeyBinding.from_lists(owner_ids, screen_names)
    except ScopeRefused as e:
        raise ProvisioningError("bad_binding", f"привязка не читается: {e}", status=400)
    for name in binding.screen_names:
        if re.fullmatch(r"-?\d+", name):
            raise ProvisioningError(
                "bad_binding", f"числовая цель {name!r} идёт в owner_ids, не в имена", status=400
            )
        if not is_valid_screen_name(name):
            raise ProvisioningError(
                "bad_binding",
                f"screen name {name!r} не в форме [a-z0-9_.] — enforcement его никогда не примет",
                status=400,
            )
    if not binding.is_bound:
        raise ProvisioningError(
            "bad_binding", "привязка пуста: нужен хотя бы один owner_id или screen name", status=400
        )
    if len(binding.owner_ids) + len(binding.screen_names) > MAX_BINDING_TARGETS:
        raise ProvisioningError(
            "bad_binding", f"слишком много целей (> {MAX_BINDING_TARGETS})", status=400
        )
    return (sorted(binding.owner_ids), sorted(binding.screen_names))


# ---------------------------------------------------------------------------
# Выдача клиента ЕСА (OIDC)
# ---------------------------------------------------------------------------
async def provision_oidc_client(
    *,
    client_id: str,
    name: str,
    redirect_uris: List[str],
    scopes: Optional[str] = None,
    public: bool = False,
    branding: Optional[Dict[str, str]] = None,
    rotate_secret: bool = False,
    current_secret: Optional[str] = None,
    allow_update: bool = False,
) -> ProvisionResult:
    """Создать или обновить OIDC-клиента ЕСА.

    ``allow_update=True`` — режим CLI (root на хосте): правит запись без
    предъявления секрета. По HTTP так нельзя: существующую запись пускаем
    править только владельцу, доказавшему владение ``current_secret``.
    """
    from database import models  # noqa: F401 — конфигурация мапперов
    from database.connection import AsyncSessionLocal
    from database.models_extended import OAuthClient
    from modules.radar.auth import hash_password, verify_password

    client_id = validate_client_id(client_id)
    name = validate_name(name)
    uris = validate_redirect_uris(redirect_uris)
    scope_str = validate_scopes(scopes)
    brand_given = validate_branding(branding)

    secret_plain: Optional[str] = None
    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(select(OAuthClient).where(OAuthClient.client_id == client_id))
        ).scalar_one_or_none()

        if row is None:
            row = OAuthClient(client_id=client_id)
            session.add(row)
            row.is_active = True
            action = "created"
            need_secret = not public
        else:
            if not allow_update:
                # Отключённого клиента self-serve НЕ воскрешает: деактивация —
                # решение оператора, и обходить его предъявлением старого
                # секрета нельзя (та же семантика, что у ключей шлюза и
                # токенов #336 — «выключено в БД» главнее всего остального).
                if not row.is_active:
                    raise ProvisioningError(
                        "client_disabled",
                        f"клиент {client_id!r} отключён оператором — включение только через него",
                        status=403,
                    )
                if not current_secret or not row.client_secret_hash:
                    raise ProvisioningError(
                        "client_exists",
                        f"client_id {client_id!r} уже занят; чтобы изменить его, "
                        "предъявите его текущий client_secret",
                        status=409,
                    )
                if not verify_password(current_secret, row.client_secret_hash):
                    raise ProvisioningError(
                        "client_secret_mismatch",
                        f"client_id {client_id!r} занят, предъявленный client_secret не подходит",
                        status=403,
                    )
            else:
                row.is_active = True
            action = "rotated" if (rotate_secret and not public) else "updated"
            need_secret = rotate_secret and not public

        row.name = name
        row.redirect_uris = list(uris)
        row.allowed_scopes = scope_str
        row.branding = merge_branding(row.branding, brand_given)
        row.is_confidential = not public
        if public:
            row.client_secret_hash = None
        elif need_secret:
            secret_plain = secrets.token_urlsafe(32)
            row.client_secret_hash = hash_password(secret_plain)
        branding_final = row.branding
        await session.commit()

    logger.info(
        "ecosystem: oidc client %s (%s), redirect_uris=%d, scopes=%s",
        client_id,
        action,
        len(uris),
        scope_str,
    )
    return ProvisionResult(
        action=action,
        identifier=client_id,
        secret=secret_plain,
        details={
            "redirect_uris": uris,
            "allowed_scopes": scope_str,
            "confidential": not public,
            "branding": branding_final,
        },
    )


# ---------------------------------------------------------------------------
# Выдача ключа VK-шлюза
# ---------------------------------------------------------------------------
async def provision_gateway_key(
    *,
    project: str,
    note: Optional[str] = None,
    rotate: bool = False,
    current_secret: Optional[str] = None,
    allow_update: bool = False,
    set_active: Optional[bool] = None,
    owner_ids: Optional[List[int]] = None,
    screen_names: Optional[List[str]] = None,
) -> ProvisionResult:
    """Выдать (или ротировать) ключ VK-шлюза проекту.

    Квота потребителя привязана к ключу (``GATEWAY_QUOTA_PER_*``) плюс общий
    бюджет шлюза, поэтому самообслуживание не расширяет поверхность нагрузки:
    лимит принадлежит ключу, а не процедуре его получения.

    ``set_active`` — только для CLI (``--disable`` / ``--enable``). Self-serve
    состояние ключа не трогает: отключённый оператором ключ не воскрешается
    предъявлением старого секрета.

    ``owner_ids``/``screen_names`` (D-047) — привязка ключа к разрешённым целям.
    На **создании** её может задать любой канал (объявить свою группу — часть
    нормальной заявки; якорь доверия self-serve — экосистемный ключ). На
    **существующем** ключе привязку меняет только оператор (``allow_update``):
    иначе держатель ключа расширил бы себе радиус предъявлением своего же
    секрета, и изоляция по владельцу перестала бы что-либо значить.
    """
    from database import models  # noqa: F401 — конфигурация мапперов
    from database.connection import AsyncSessionLocal
    from database.models import GatewayKey
    from modules.gateway.keys import gateway_secret_prefix, hash_gateway_secret
    from modules.gateway.usage import record_request

    name = validate_project_name(project)
    binding_requested = owner_ids is not None or screen_names is not None
    normalized_binding = None
    if binding_requested:
        normalized_binding = _validate_gateway_binding(owner_ids, screen_names)

    secret_plain: Optional[str] = None
    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(select(GatewayKey).where(GatewayKey.name == name))
        ).scalar_one_or_none()

        if row is None:
            secret_plain = secrets.token_urlsafe(32)
            row = GatewayKey(
                name=name,
                secret_sha256=hash_gateway_secret(secret_plain),
                secret_prefix=gateway_secret_prefix(secret_plain),
                is_active=set_active is not False,
                note=note,
            )
            if normalized_binding is not None:
                row.allowed_owner_ids = normalized_binding[0]
                row.allowed_screen_names = normalized_binding[1]
            session.add(row)
            action = "created"
        else:
            if binding_requested and not allow_update:
                raise ProvisioningError(
                    "binding_operator_only",
                    f"привязку существующего ключа {name!r} меняет только оператор (D-047)",
                    status=403,
                )
            if not allow_update:
                if not row.is_active:
                    raise ProvisioningError(
                        "key_disabled",
                        f"ключ проекта {name!r} отключён оператором — включение только через него",
                        status=403,
                    )
                if not current_secret or not row.secret_sha256:
                    raise ProvisioningError(
                        "key_exists",
                        f"ключ проекта {name!r} уже выдан; для ротации предъявите текущий ключ",
                        status=409,
                    )
                # Сверяем хэши: самого секрета у нас нет (миграции 074/075).
                if not hmac.compare_digest(hash_gateway_secret(current_secret), row.secret_sha256):
                    raise ProvisioningError(
                        "key_mismatch",
                        f"ключ проекта {name!r} выдан, предъявленный ключ не подходит",
                        status=403,
                    )
            if set_active is not None:
                row.is_active = set_active
                action = "enabled" if set_active else "disabled"
            elif rotate:
                secret_plain = secrets.token_urlsafe(32)
                row.secret_sha256 = hash_gateway_secret(secret_plain)
                row.secret_prefix = gateway_secret_prefix(secret_plain)
                row.secret = None  # plaintext больше не хранится (мандат brain 2026-08-01)
                row.rotated_at = datetime.utcnow()
                action = "rotated"
            else:
                action = "unchanged"
            if normalized_binding is not None:  # сюда доходит только оператор (гейт выше)
                row.allowed_owner_ids = normalized_binding[0]
                row.allowed_screen_names = normalized_binding[1]
                if action == "unchanged":
                    action = "rebound"
        if note is not None:
            row.note = note
        active = bool(row.is_active)
        bound_ids = list(row.allowed_owner_ids or [])
        bound_names = list(row.allowed_screen_names or [])
        await session.commit()

    # Событие выдачи видно на /gateway-stats (best-effort, #018).
    try:
        await record_request(name, "issue-key", action, None, status=200, ok=True)
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("ecosystem: usage-log failed for %s: %s", name, e)

    logger.info("ecosystem: gateway key %s (%s)", name, action)
    return ProvisionResult(
        action=action,
        identifier=name,
        secret=secret_plain,
        details={"active": active, "owner_ids": bound_ids, "screen_names": bound_names},
    )
