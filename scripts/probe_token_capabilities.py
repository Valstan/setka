#!/usr/bin/env python3
"""Эмпирическая матрица «токен × метод VK» — что реально умеет каждый вид токена.

Зачем. Маршрутизация токенов (:mod:`modules.vk_token_router`) раскладывает
операции по токенам, исходя из предположений о правах. Предположения устаревают:
VK меняет политику, аккаунты банят и разбанивают, scope'ы отзывают. Этот скрипт
проверяет права **опытом**, а не по памяти, и печатает таблицу, которую можно
положить в документацию (`docs/VK_TOKEN_ROADMAP.md`).

Только **read-only** методы. Ничего не публикует, не комментирует, не лайкает и
не шлёт сообщений — матрица прав на запись выводится из scope
(``groups.getTokenPermissions`` / ``account.getAppPermissions``) и из журналов
прода, а не из живых записей в чужие стены.

Использование (на проде, токены подаются в stdin, чтобы не светиться в argv):

    sudo -u postgres psql -d setka -tAF$'\\t' \\
        -c "SELECT name, token FROM vk_tokens WHERE is_active" \\
      | python3 scripts/probe_token_capabilities.py \\
          --own-group 158787639 --foreign-group 168170215 --donor-group 24611937

Значения токенов никогда не попадают ни в stdout, ни в argv.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

VK_API = "https://api.vk.com/method/"
VK_VERSION = "5.131"
PAUSE_SEC = 0.34  # VK: 3 запроса/с на токен


def call(token: str, method: str, params: Dict[str, Any]) -> Tuple[bool, str, Any]:
    """Вызвать метод VK. Возвращает ``(ok, короткая_метка, сырой_ответ)``."""
    payload = dict(params)
    payload["access_token"] = token
    payload["v"] = VK_VERSION
    data = urllib.parse.urlencode(payload).encode()
    req = urllib.request.Request(VK_API + method, data=data)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = json.loads(resp.read().decode())
    except Exception as exc:  # noqa: BLE001 — сетевой сбой тоже результат замера
        return False, f"net:{type(exc).__name__}", None
    if "error" in body:
        err = body["error"]
        return False, f"err{err.get('error_code')}", err.get("error_msg", "")
    return True, "ok", body.get("response")


def probe_matrix(
    own: int, foreign: int, donor: int, post_id: Optional[int]
) -> List[Tuple[str, str, Dict[str, Any]]]:
    """Список проб ``(ярлык, метод, параметры)``. Только чтение."""
    probes: List[Tuple[str, str, Dict[str, Any]]] = [
        ("users.get", "users.get", {}),
        ("groups.getById(своё)", "groups.getById", {"group_id": own}),
        ("groups.getById(донор)", "groups.getById", {"group_id": donor}),
        ("groups.get(свои)", "groups.get", {"count": 1}),
        ("wall.get(своё)", "wall.get", {"owner_id": -own, "count": 1}),
        ("wall.get(наше чужое)", "wall.get", {"owner_id": -foreign, "count": 1}),
        ("wall.get(донор)", "wall.get", {"owner_id": -donor, "count": 1}),
        ("groups.search", "groups.search", {"q": "Малмыж", "count": 1}),
        ("groups.getMembers(своё)", "groups.getMembers", {"group_id": own, "count": 1}),
        ("groups.getMembers(донор)", "groups.getMembers", {"group_id": donor, "count": 1}),
        ("utils.resolveScreenName", "utils.resolveScreenName", {"screen_name": "vk"}),
        ("messages.getConversations", "messages.getConversations", {"count": 1}),
        ("photos.getWallUploadServer(своё)", "photos.getWallUploadServer", {"group_id": own}),
        (
            "photos.getWallUploadServer(наше чужое)",
            "photos.getWallUploadServer",
            {"group_id": foreign},
        ),
        ("groups.getTokenPermissions", "groups.getTokenPermissions", {}),
        ("account.getAppPermissions", "account.getAppPermissions", {}),
    ]
    if post_id is not None:
        probes.insert(
            8,
            (
                "wall.getComments(своё)",
                "wall.getComments",
                {"owner_id": -own, "post_id": post_id, "count": 1},
            ),
        )
    return probes


def read_tokens() -> List[Tuple[str, str]]:
    """Прочитать ``name<TAB>token`` из stdin."""
    out: List[Tuple[str, str]] = []
    for line in sys.stdin:
        line = line.rstrip("\n")
        if not line or "\t" not in line:
            continue
        name, token = line.split("\t", 1)
        if name.strip() and token.strip():
            out.append((name.strip(), token.strip()))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--own-group", type=int, required=True, help="abs(id) своего сообщества")
    ap.add_argument("--foreign-group", type=int, required=True, help="abs(id) другого нашего")
    ap.add_argument("--donor-group", type=int, required=True, help="abs(id) стороннего донора")
    ap.add_argument("--only", default="", help="проверять только эти токены (через запятую)")
    args = ap.parse_args()

    tokens = read_tokens()
    if args.only:
        wanted = {n.strip().upper() for n in args.only.split(",")}
        tokens = [(n, t) for n, t in tokens if n.upper() in wanted]
    if not tokens:
        print("нет токенов на входе", file=sys.stderr)
        return 1

    own = abs(args.own_group)
    foreign = abs(args.foreign_group)
    donor = abs(args.donor_group)

    # Один post_id на все токены — берём первым же токеном, у которого получилось.
    post_id: Optional[int] = None
    for _, token in tokens:
        ok, _, resp = call(token, "wall.get", {"owner_id": -own, "count": 1})
        time.sleep(PAUSE_SEC)
        if ok and isinstance(resp, dict) and resp.get("items"):
            post_id = resp["items"][0].get("id")
            break

    probes = probe_matrix(own, foreign, donor, post_id)
    results: Dict[str, Dict[str, str]] = {}

    for name, token in tokens:
        row: Dict[str, str] = {}
        for label, method, params in probes:
            ok, tag, extra = call(token, method, params)
            if ok:
                row[label] = "ok"
            else:
                msg = extra if isinstance(extra, str) else ""
                row[label] = f"{tag}: {msg[:60]}" if msg else tag
            time.sleep(PAUSE_SEC)
        results[name] = row
        print(f"# {name} — готово", file=sys.stderr)

    labels = [p[0] for p in probes]
    names = [n for n, _ in tokens]
    print(f"\npost_id для wall.getComments: {post_id}\n")
    print("| Метод | " + " | ".join(names) + " |")
    print("|---" * (len(names) + 1) + "|")
    for label in labels:
        cells = [results[n].get(label, "—") for n in names]
        print(f"| {label} | " + " | ".join(cells) + " |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
