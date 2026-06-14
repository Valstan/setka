"""Probe VK-сообщений сообщества — для VK-лички радара («Радиоточка»).

Probe-before-build (#020): до постройки VK-личка-доставки выясняем главный
неизвестный — **есть ли у нас community-токен с правом на сообщения и работает ли
Bots Long Poll** (чтобы ловить входящие сообщения юзеров → их vk_id для привязки,
аналог Telegram getUpdates), и **можем ли слать `messages.send`**.

ЧИСТО ЧИТАЮЩИЙ / НЕ-DESTRUCTIVE:
- ``groups.getById(fields=...)`` — статус сообщений сообщества [read];
- ``groups.getLongPollServer(group_id)`` — возвращает адрес long-poll сервера и
  НИЧЕГО не меняет; успех = у токена есть messages-scope и LP включён, ошибка
  (15/27/100/...) = нет права/выключено. Сообщения НЕ шлём.

Запуск на проде: sudo + source /etc/setka/setka.env (root-only env), затем
``./venv/bin/python scripts/probe_vk_messaging.py``. Вывод — JSON в stdout.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict

import httpx

VK_API = "https://api.vk.com/method/{method}"
VK_V = "5.199"


async def _call(client: httpx.AsyncClient, method: str, token: str, **params) -> Dict[str, Any]:
    params.update({"access_token": token, "v": VK_V})
    try:
        r = await client.get(VK_API.format(method=method), params=params, timeout=20)
        return r.json()
    except Exception as e:  # noqa: BLE001
        return {"error": {"error_msg": str(e)}}


async def main() -> None:
    from modules.vk_token_router import load_vk_routing

    user_token, community_tokens = await load_vk_routing()
    community_tokens = community_tokens or {}

    summary: Dict[str, Any] = {
        "community_tokens_count": len(community_tokens),
        "has_user_token": bool(user_token),
        "communities": [],
    }

    async with httpx.AsyncClient() as client:
        for gid, token in community_tokens.items():
            group_id = abs(int(gid))
            entry: Dict[str, Any] = {"group_id": group_id}

            # 1. Статус сообщений сообщества (нужен user/community токен).
            info = await _call(
                client,
                "groups.getById",
                token,
                group_id=group_id,
                fields="can_message,is_messages_blocked",
            )
            groups = (
                (info.get("response") or {}).get("groups")
                if isinstance(info.get("response"), dict)
                else info.get("response")
            )
            if isinstance(groups, list) and groups:
                g = groups[0]
                entry["name"] = g.get("name")
                entry["can_message"] = g.get("can_message")
            elif "error" in info:
                entry["getById_error"] = info["error"].get("error_msg")

            # 2. Bots Long Poll сервер (главный probe: messages-scope + LP включён).
            lp = await _call(client, "groups.getLongPollServer", token, group_id=group_id)
            if "response" in lp and (lp["response"] or {}).get("server"):
                entry["long_poll"] = "ok"
            else:
                err = lp.get("error") or {}
                entry["long_poll"] = f"FAIL [{err.get('error_code')}] {err.get('error_msg')}"

            summary["communities"].append(entry)

    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
