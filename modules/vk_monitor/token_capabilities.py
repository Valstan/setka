"""Замер прав VK-токенов опытом: «кто какой метод реально может» (2026-07-26).

Зачем. Маршрутизация токенов исходит из предположений о правах, а VK меняет
политику молча: community-токен, который вчера читал стену, сегодня отвечает
error 27, и узнаём мы об этом по сломанному сбору. Разовый ручной замер
(``scripts/probe_token_capabilities.py``, матрица в ``docs/VK_TOKEN_ROADMAP.md``)
отвечает на вопрос «как было 25 июля», но не замечает дрейфа.

Что делает модуль. Гоняет короткий набор **read-only** проб по каждому токену и
складывает результат снапшотом в Redis. Ничего не публикует, не комментирует, не
лайкает и не шлёт сообщений — права на запись выводятся из scope и журналов
прода, а не из живых записей в чужие стены.

Почему раз в месяц (решение владельца 2026-07-26). Ежедневный замер жёг бы
квоту без пользы: права меняются редко, а каждый прогон — это ``число_токенов ×
число_проб`` запросов. Раз в месяц ловит дрейф до того, как он станет
инцидентом, и стоит околонуля.

Экономия проб. Community-токены ведут себя одинаково (это подтвердил замер
25.07: у всех 19 одна и та же матрица), поэтому меряем **все user-токены и до
двух community** — representative выборка вместо полного перебора.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

VK_API = "https://api.vk.com/method/"
VK_VERSION = "5.131"
PAUSE_SEC = 0.34  # VK: 3 запроса/с на токен

SNAPSHOT_KEY = "setka:vk_capabilities:latest"
SNAPSHOT_TTL_SECONDS = 400 * 24 * 3600  # снапшот переживает год без прогона

# Сколько community-токенов меряем: матрица у них одинаковая, второй нужен лишь
# чтобы отличить «права изменились у всех» от «конкретный ключ протух».
COMMUNITY_SAMPLE = 2

# Ориентиры для проб: своё сообщество, «наше чужое», донор-источник. Те же, что
# в ручном скрипте, — чтобы матрицы были сравнимы между прогонами.
DEFAULT_OWN_GROUP = 158787639  # МАЛМЫЖ - ИНФО
DEFAULT_FOREIGN_GROUP = 168170215  # УРЖУМ - ИНФО (наше, но не «своё» для токена)
DEFAULT_DONOR_GROUP = 24611937  # ОБЪЯВЛЕНИЯ г МАЛМЫЖ (чужой донор)


def build_probes(
    own: int = DEFAULT_OWN_GROUP,
    foreign: int = DEFAULT_FOREIGN_GROUP,
    donor: int = DEFAULT_DONOR_GROUP,
) -> List[Tuple[str, str, Dict[str, Any]]]:
    """``[(ярлык, метод, параметры)]`` — только чтение.

    Набор сознательно короче ручного скрипта: здесь важен **дрейф** ключевых
    способностей, на которых стоит маршрутизация, а не полная матрица для
    документации.
    """
    return [
        ("users.get", "users.get", {}),
        ("groups.getById(своё)", "groups.getById", {"group_id": own}),
        ("wall.get(своё)", "wall.get", {"owner_id": -own, "count": 1}),
        ("wall.get(чужое)", "wall.get", {"owner_id": -donor, "count": 1}),
        ("groups.search", "groups.search", {"q": "Малмыж", "count": 1}),
        ("groups.getMembers(чужое)", "groups.getMembers", {"group_id": donor, "count": 1}),
        ("messages.getConversations", "messages.getConversations", {"count": 1}),
        ("photos.getWallUploadServer", "photos.getWallUploadServer", {"group_id": own}),
        ("groups.getTokenPermissions", "groups.getTokenPermissions", {}),
        ("utils.resolveScreenName", "utils.resolveScreenName", {"screen_name": "vk"}),
        ("groups.getById(чужое)", "groups.getById", {"group_id": foreign}),
    ]


def call(token: str, method: str, params: Dict[str, Any], timeout: int = 20) -> str:
    """Вызвать метод VK, вернуть короткую метку: ``ok`` / ``errNN`` / ``net:*``.

    Значение токена никогда не попадает ни в лог, ни в результат.
    """
    payload = dict(params)
    payload["access_token"] = token
    payload["v"] = VK_VERSION
    data = urllib.parse.urlencode(payload).encode()
    request = urllib.request.Request(VK_API + method, data=data)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode())
    except Exception as exc:  # noqa: BLE001 — сетевой сбой тоже результат замера
        return f"net:{type(exc).__name__}"
    if "error" in body:
        return f"err{body['error'].get('error_code')}"
    return "ok"


def select_tokens(tokens: Dict[str, str]) -> Dict[str, str]:
    """Все user-токены + до ``COMMUNITY_SAMPLE`` community — экономия квоты."""
    users = {name: value for name, value in tokens.items() if not name.upper().startswith("COMM_")}
    communities = sorted(name for name in tokens if name.upper().startswith("COMM_"))
    sample = {name: tokens[name] for name in communities[:COMMUNITY_SAMPLE]}
    return {**users, **sample}


def measure(tokens: Dict[str, str], probes=None) -> Dict[str, Dict[str, str]]:
    """``{имя_токена: {ярлык_пробы: метка}}``. Пропускает пустые токены."""
    probes = probes or build_probes()
    matrix: Dict[str, Dict[str, str]] = {}
    for name, token in tokens.items():
        if not token:
            continue
        row: Dict[str, str] = {}
        for label, method, params in probes:
            row[label] = call(token, method, params)
            time.sleep(PAUSE_SEC)
        matrix[name] = row
        logger.info(
            "token_capabilities: %s — ok=%d из %d",
            name,
            sum(1 for v in row.values() if v == "ok"),
            len(row),
        )
    return matrix


def _redis():
    from modules.vk_monitor.token_usage import _get_redis

    return _get_redis()


def save_snapshot(matrix: Dict[str, Dict[str, str]], measured_at: str) -> bool:
    """Сохранить снапшот в Redis. ``False`` — Redis недоступен (не критично)."""
    client = _redis()
    if client is None:
        return False
    try:
        client.set(
            SNAPSHOT_KEY,
            json.dumps({"measured_at": measured_at, "matrix": matrix}, ensure_ascii=False),
            ex=SNAPSHOT_TTL_SECONDS,
        )
        return True
    except Exception as exc:  # pragma: no cover — отчёт не критичен
        logger.warning("token_capabilities.save_snapshot failed: %s", exc)
        return False


def load_snapshot() -> Optional[Dict[str, Any]]:
    """Последний снапшот или ``None``, если замера ещё не было."""
    client = _redis()
    if client is None:
        return None
    try:
        raw = client.get(SNAPSHOT_KEY)
        return json.loads(raw) if raw else None
    except Exception as exc:  # pragma: no cover
        logger.warning("token_capabilities.load_snapshot failed: %s", exc)
        return None


def diff_snapshots(
    previous: Optional[Dict[str, Dict[str, str]]],
    current: Dict[str, Dict[str, str]],
) -> List[str]:
    """Что изменилось между замерами — человекочитаемыми строками.

    Пустой список = дрейфа нет. Именно ради этого списка и заводится
    периодический замер: сама матрица меняется редко, ценность в том, чтобы
    изменение не прошло незамеченным.
    """
    if not previous:
        return []
    changes: List[str] = []
    for name, row in sorted(current.items()):
        before = previous.get(name)
        if before is None:
            changes.append(f"{name}: новый токен в замере")
            continue
        for label, value in sorted(row.items()):
            old = before.get(label)
            if old is not None and old != value:
                changes.append(f"{name} · {label}: {old} → {value}")
    for name in sorted(set(previous) - set(current)):
        changes.append(f"{name}: пропал из замера")
    return changes
