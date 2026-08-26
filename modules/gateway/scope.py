"""D-047: привязка ключей VK-шлюза к разрешённым owner_id (решение владельца 25.08).

Замер 2026-08-25 показал: любой ключ читал любую публичную стену — модель «ключ
для своей стены», которую потребитель себе естественно рисует, была неверна.
Здесь — вторая половина контракта: у ключа список разрешённых целей
(``gateway_keys.allowed_owner_ids`` + ``allowed_screen_names``, миграция 085),
и каждый вызов проверяется против него в ``_run_and_log``.

Принципы (все — fail-closed):

* **Цель обязана быть явной.** У большинства owner-scoped методов VK умолчание —
  «текущий пользователь», то есть владелец НАШЕГО токена: ``video.get`` без
  ``owner_id`` отдал бы видео аккаунта SETKA потребителю. Отсутствие цели — отказ.
* **Ключ без привязки — отказ** по owner-scoped методам (мандат: не «читает всё»).
  NULL и пустой список равнозначны. Глобальные методы без конкретного владельца
  (поиск, справочники) остаются доступны аутентифицированному ключу.
* **Незнакомый метод — отказ.** Метод, добавленный в ``GATEWAY_READ_METHODS`` без
  правила экстракции здесь, не проскакивает молча; полноту карты держит тест
  ``test_extraction_map_covers_entire_allowlist``.
* **Ошибка проверки — отказ.** Любой сбой самого механизма (БД, неожиданный тип)
  трактуется как запрет, не как пропуск.

Положительный id группы в ``group_id``/``group_ids`` эквивалентен owner ``-id``
(конвенция VK). Screen names сравниваются в нижнем регистре.

⚠️ Screen name у VK переназначаем: ``allowed_screen_names`` авторизует того, кто
держит имя СЕГОДНЯ. Источник истины привязки — числовые id; имена — алиасы для
удобства потребителя (их и передают живые вызовы), дрейф имени — принятый риск,
зафиксированный здесь, а не молчаливый.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, FrozenSet, Iterable, List, Optional, Union

logger = logging.getLogger(__name__)

# Цель вызова: подписанный owner_id (int) либо screen name (str, lowercase).
Target = Union[int, str]

# Методы без конкретного владельца: поиск/справочники/резолв имени. Разрешены
# любому аутентифицированному ключу — они не читают стену конкретного владельца.
GLOBAL_METHODS: FrozenSet[str] = frozenset(
    {
        "groups.search",
        "newsfeed.search",
        "database.getCities",
        "database.getCountries",
        "utils.resolveScreenName",
    }
)

_SCREEN_NAME_RE = re.compile(r"^[a-z0-9_.]{1,64}$")
_POST_REF_RE = re.compile(r"^(-?\d{1,20})_\d{1,20}$")


class ScopeRefused(Exception):
    """Цель вызова не извлекается или не разрешена (наружу уходит 403)."""


def is_valid_screen_name(value: str) -> bool:
    """Форма screen name, которую примет экстрактор (lowercase, [a-z0-9_.]).

    Выдача ключей валидирует привязку этим же предикатом: имя, не проходящее
    здесь, никогда не совпало бы с целью запроса — мёртвая запись в привязке.
    """
    return bool(_SCREEN_NAME_RE.fullmatch(value))


@dataclass(frozen=True)
class KeyBinding:
    """Привязка ключа: разрешённые owner_id и screen names (нормализованные)."""

    owner_ids: FrozenSet[int]
    screen_names: FrozenSet[str]

    @classmethod
    def from_lists(
        cls,
        owner_ids: Optional[Iterable[Any]],
        screen_names: Optional[Iterable[Any]] = None,
    ) -> "KeyBinding":
        ids = frozenset(_as_int(v) for v in (owner_ids or []))
        names = frozenset(str(v).strip().lower() for v in (screen_names or []) if str(v).strip())
        return cls(owner_ids=ids, screen_names=names)

    @property
    def is_bound(self) -> bool:
        return bool(self.owner_ids or self.screen_names)

    def allows(self, target: Target) -> bool:
        if isinstance(target, int):
            return target in self.owner_ids
        return target in self.screen_names


def _as_int(value: Any) -> int:
    """Строгий int: bool/float/мусор — отказ (не «похоже на число — сойдёт»)."""
    if isinstance(value, bool):
        raise ScopeRefused(f"not an id: {value!r}")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        v = value.strip()
        if re.fullmatch(r"-?\d{1,20}", v):
            return int(v)
    raise ScopeRefused(f"not an id: {value!r}")


def _as_target(value: Any) -> Target:
    """Элемент CSV/списка: число → int, screen name → lowercase str, иное — отказ."""
    if isinstance(value, bool):
        raise ScopeRefused(f"bad target: {value!r}")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        v = value.strip()
        if re.fullmatch(r"-?\d{1,20}", v):
            return int(v)
        low = v.lower()
        if _SCREEN_NAME_RE.fullmatch(low):
            return low
    raise ScopeRefused(f"bad target: {value!r}")


def _csv_items(value: Any, param: str) -> List[Any]:
    """CSV-строка либо готовый список; пусто — отказ (цель обязана быть явной)."""
    if value is None:
        raise ScopeRefused(f"required param missing: {param}")
    if isinstance(value, (list, tuple)):
        items: List[Any] = list(value)
    elif isinstance(value, str):
        items = [p for p in (s.strip() for s in value.split(",")) if p]
    elif isinstance(value, int) and not isinstance(value, bool):
        items = [value]
    else:
        raise ScopeRefused(f"bad param type: {param}")
    if not items:
        raise ScopeRefused(f"required param missing: {param}")
    return items


def _owner_required(params: Dict[str, Any]) -> List[Target]:
    """Методы с обязательным ``owner_id`` (умолчание VK — владелец токена)."""
    if "owner_id" not in params or params.get("owner_id") is None:
        raise ScopeRefused("required param missing: owner_id")
    return [_as_int(params["owner_id"])]


def _wall_get(params: Dict[str, Any]) -> List[Target]:
    """``wall.get``: owner_id и/или domain — проверяются ВСЕ присутствующие.

    Не «первый попавшийся»: params уходят в VK как есть, и чей приоритет у VK
    при обоих параметрах сразу — недокументировано. Пара «свой owner_id + чужой
    domain» с проверкой только owner_id была бы контрабандой (блокер
    adversarial-ревью 2026-08-26).
    """
    targets: List[Target] = []
    if params.get("owner_id") is not None:
        targets.append(_as_int(params["owner_id"]))
    domain = params.get("domain")
    if domain is not None:
        if not isinstance(domain, str) or not domain.strip():
            raise ScopeRefused(f"bad target: {domain!r}")
        target = _as_target(domain)
        if isinstance(target, int):  # domain с числом — не то, что ждёт VK
            raise ScopeRefused(f"bad target: {domain!r}")
        targets.append(target)
    if not targets:
        raise ScopeRefused("required param missing: owner_id or domain")
    return targets


def _wall_get_by_id(params: Dict[str, Any]) -> List[Target]:
    """``wall.getById``: posts = CSV из ``{owner_id}_{post_id}``."""
    targets: List[Target] = []
    for item in _csv_items(params.get("posts"), "posts"):
        m = _POST_REF_RE.fullmatch(str(item).strip())
        if not m:
            raise ScopeRefused(f"bad post ref: {item!r}")
        targets.append(int(m.group(1)))
    return targets


def _group_targets(value: Any, param: str) -> List[Target]:
    """group_id/group_ids: положительное число N — это owner ``-N``; имя — имя."""
    targets: List[Target] = []
    for item in _csv_items(value, param):
        t = _as_target(item)
        if isinstance(t, int):
            if t <= 0:
                raise ScopeRefused(f"bad group id: {item!r}")
            t = -t
        targets.append(t)
    return targets


def _groups_get_by_id(params: Dict[str, Any]) -> List[Target]:
    """``groups.getById``: group_ids и/или group_id — проверяются оба алиаса.

    Та же анти-контрабанда, что в ``_wall_get``: присутствуют оба — целями
    становятся элементы обоих.
    """
    targets: List[Target] = []
    if params.get("group_ids") is not None:
        targets += _group_targets(params["group_ids"], "group_ids")
    if params.get("group_id") is not None:
        targets += _group_targets(params["group_id"], "group_id")
    if not targets:
        raise ScopeRefused("required param missing: group_ids")
    return targets


def _group_id_required(params: Dict[str, Any]) -> List[Target]:
    return _group_targets(params.get("group_id"), "group_id")


def _users_get(params: Dict[str, Any]) -> List[Target]:
    """``users.get``: user_ids CSV; без него VK вернул бы владельца токена."""
    targets: List[Target] = []
    for item in _csv_items(params.get("user_ids"), "user_ids"):
        t = _as_target(item)
        if isinstance(t, int) and t <= 0:
            raise ScopeRefused(f"bad user id: {item!r}")
        targets.append(t)
    return targets


def _user_id_required(params: Dict[str, Any]) -> List[Target]:
    if params.get("user_id") is None:
        raise ScopeRefused("required param missing: user_id")
    uid = _as_int(params["user_id"])
    if uid <= 0:
        raise ScopeRefused(f"bad user id: {params['user_id']!r}")
    return [uid]


def _video_get(params: Dict[str, Any]) -> List[Target]:
    """``video.get``: цель — ``owner_id`` И/ИЛИ владельцы из ``videos``.

    Элемент ``videos`` — ``{owner}_{video_id}[_{access_key}]``: владелец — до
    первого ``_``, каждый обязан быть в привязке. Живой пример — CDK_KALININO
    зовёт только с ``videos`` (замер 2026-08-26), поэтому ``owner_id`` не
    обязателен при явных ``videos``. Нет ни того, ни другого — VK вернул бы
    видео владельца НАШЕГО токена: отказ.
    """
    targets: List[Target] = []
    if params.get("owner_id") is not None:
        targets.append(_as_int(params["owner_id"]))
    if params.get("videos") is not None:
        for item in _csv_items(params["videos"], "videos"):
            owner_part = str(item).strip().split("_", 1)[0]
            if not re.fullmatch(r"-?\d{1,20}", owner_part):
                raise ScopeRefused(f"bad video ref: {item!r}")
            targets.append(int(owner_part))
    if not targets:
        raise ScopeRefused("required param missing: owner_id or videos")
    return targets


_EXTRACTORS: Dict[str, Callable[[Dict[str, Any]], List[Target]]] = {
    "wall.get": _wall_get,
    "wall.getById": _wall_get_by_id,
    "wall.getComments": _owner_required,
    "wall.getReposts": _owner_required,
    "photos.get": _owner_required,
    "photos.getAlbums": _owner_required,
    "video.get": _video_get,
    "likes.getList": _owner_required,
    "stats.getPostReach": _owner_required,
    "groups.getById": _groups_get_by_id,
    "groups.getMembers": _group_id_required,
    "groups.isMember": _group_id_required,
    "board.getTopics": _group_id_required,
    "board.getComments": _group_id_required,
    "users.get": _users_get,
    "users.getFollowers": _user_id_required,
    "users.getSubscriptions": _user_id_required,
}


def extract_targets(method: str, params: Dict[str, Any]) -> List[Target]:
    """Цели вызова; ``ScopeRefused``, если метод неизвестен или цель не явная."""
    extractor = _EXTRACTORS.get(method)
    if extractor is None:
        raise ScopeRefused(f"no scope rule for method: {method}")
    return extractor(dict(params or {}))


def check_method_scope(
    method: str, params: Dict[str, Any], binding: Optional[KeyBinding]
) -> Optional[str]:
    """``None`` — вызов разрешён; иначе причина отказа (наружу уходит 403).

    Fail-closed: сбой самой проверки — тоже отказ, не пропуск.
    """
    try:
        if method in GLOBAL_METHODS:
            return None
        if binding is None or not binding.is_bound:
            return "key has no owner binding; ask the operator to bind your community"
        targets = extract_targets(method, params)
        if not targets:
            # Экстракторы обязаны отказывать сами; этот пояс — на случай, если
            # будущий экстрактор вернёт пусто: «все из нуля разрешены» не бывает.
            return "no explicit target in params"
        for target in targets:
            if not binding.allows(target):
                return f"owner not allowed for this key: {target}"
        return None
    except ScopeRefused as e:
        return str(e)
    except Exception:  # noqa: BLE001 — сбой механизма проверки не открывает дверь
        logger.exception("gateway scope check failed for %s — refusing", method)
        return "scope check failed"


class BindingLoadError(Exception):
    """БД недоступна — привязку прочитать нельзя (это не «не привязан»)."""


async def load_binding(key_name: str) -> Optional[KeyBinding]:
    """Привязка ключа из БД; ``None`` — строки нет или привязка пуста.

    Env-bootstrap ключи (без строки в БД) привязки не имеют — по мандату они
    получают отказ на owner-scoped методах, глобальные методы остаются.
    Недоступная БД — ``BindingLoadError``: потребитель с честной привязкой
    должен увидеть «временно», а не «тебя не привязали» (ревью 2026-08-26).
    Кривая форма колонок (не список — рукотворный UPDATE мимо валидации) —
    warning и «не привязан»: молча пропустить нельзя, ронять шлюз тоже.
    """
    try:
        from sqlalchemy import select

        from database.connection import AsyncSessionLocal
        from database.models import GatewayKey

        async with AsyncSessionLocal() as session:
            row = (
                await session.execute(select(GatewayKey).where(GatewayKey.name == key_name))
            ).scalar_one_or_none()
    except Exception as e:
        logger.warning("gateway scope: binding load failed for %s: %s", key_name, e)
        raise BindingLoadError(str(e)) from e
    if row is None:
        return None
    ids, names = row.allowed_owner_ids, row.allowed_screen_names
    if (ids is not None and not isinstance(ids, list)) or (
        names is not None and not isinstance(names, list)
    ):
        logger.warning("gateway scope: malformed binding for %s — treating as unbound", key_name)
        return None
    try:
        return KeyBinding.from_lists(ids, names)
    except ScopeRefused:
        logger.warning("gateway scope: unreadable binding for %s — treating as unbound", key_name)
        return None


async def check_call_scope(key_name: str, method: str, params: Dict[str, Any]) -> Optional[str]:
    """Точка входа для шлюза: загрузить привязку и проверить вызов.

    ``None`` — разрешён; строка — причина отказа. Недоступность БД отличается
    от «не привязан» текстом: fail-closed в обоих случаях, но диагностика
    потребителя не врёт о причине.
    """
    if method in GLOBAL_METHODS:
        return None
    try:
        binding = await load_binding(key_name)
    except BindingLoadError:
        return "owner binding temporarily unavailable — retry later"
    return check_method_scope(method, params, binding)
