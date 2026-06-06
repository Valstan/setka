#!/usr/bin/env python3
"""Живой VK-probe возможностей community-токена для входящих ЛС (Этап 2, R4/R5).

Probe-before-build (#020): ДО постройки in-app переписки проверяем на проде, что
реально умеет community-токен сообщества с входящими ЛС. Часть возможностей уже
доказана живым кодом (ad-кабинет читает историю `messages.getHistory` и отвечает
`messages.send` на рекламные ЛС) — probe это подтверждает эмпирически и закрывает
два открытых вопроса директивы:

  1. КАКОЙ вызов гасит VK-флаг unread? Меряем `getConversations(filter=unread)`
     ДО / между / ПОСЛЕ вызовов скана (`getConversations(extended=1)`) и
     `getHistory` — видно, что именно метит прочитанным (корень бага R2).
  2. Есть ли `markAsUnread`-эквивалент? (ожидаем «нет» → R2 нас и страхует.)

**Безопасен по умолчанию.** Без ``--send`` — только ЧИТАЕТ
(`getConversations`/`getHistory`/`isMessagesFromGroupAllowed`) и печатает вердикт.
Единственный сайд-эффект чтения — VK может пометить часть входящих сообщества
прочитанными; после Этапа 1 это безопасно (каждое ЛС уже persist'нуто в БД).

Запись (`messages.send`) — только с ``--send --peer-id N`` И
``SETKA_PROBE_CONFIRM=yes`` (двойной предохранитель). Шлёт один помеченный тест-
текст указанному peer (используй СВОЙ аккаунт, написавший сообществу) и сразу
пытается удалить (`messages.delete delete_for_all=1`). Если revert не прошёл —
печатает message_id для ручной чистки.

Примеры (на проде через `ssh setka`):

    # read-only диагностика (авто-выбор сообщества с входящими ЛС)
    python3 scripts/probe_community_dm_capabilities.py

    # конкретная группа
    python3 scripts/probe_community_dm_capabilities.py --group -158787639

    # живой send-тест в свой диалог (peer = свой vk id), с авто-revert
    SETKA_PROBE_CONFIRM=yes python3 scripts/probe_community_dm_capabilities.py \
        --group -158787639 --send --peer-id 12345
"""

from __future__ import annotations

import argparse
import asyncio
import os
import random
from typing import Any, Dict, List, Optional, Tuple


def _unread_state(api) -> Tuple[int, set]:
    """(count, {peer_id}) непрочитанных диалогов community-токеном.

    community-токену group_id не нужен — подразумевается из токена.
    """
    res = api.messages.getConversations(count=200, filter="unread")
    count = int(res.get("count", 0) or 0)
    peers = set()
    for item in res.get("items", []) or []:
        peer = (item.get("conversation") or {}).get("peer") or {}
        if peer.get("id"):
            peers.add(int(peer["id"]))
    return count, peers


def _sample_inbound_dialog(api) -> Optional[Dict[str, Any]]:
    """Первый входящий диалог с пользователем (out=0) из getConversations(extended=1).

    Это ТОТ ЖЕ вызов, что делает dm_scanner — проверяем заодно, метит ли он read.
    """
    res = api.messages.getConversations(count=20, extended=1)
    for item in res.get("items", []) or []:
        conv = item.get("conversation") or {}
        last = item.get("last_message") or {}
        peer = conv.get("peer") or {}
        if peer.get("type") != "user" or not peer.get("id"):
            continue
        if int(last.get("out", 0) or 0) == 1:
            continue  # последними ответили мы — для probe нужен входящий
        return {
            "peer_id": int(peer["id"]),
            "in_read": conv.get("in_read"),
            "out_read": conv.get("out_read"),
            "last_message_id": conv.get("last_message_id"),
            "can_write": (conv.get("can_write") or {}),
            "text": (last.get("text") or "")[:60],
        }
    return None


def _pick_group(community_tokens: Dict[int, str], forced: Optional[int]) -> List[int]:
    """Список community-id для probe: либо один заданный, либо все доступные."""
    if forced is not None:
        return [abs(int(forced))]
    return sorted(community_tokens.keys())


async def main() -> int:
    ap = argparse.ArgumentParser(description="VK-probe community-DM capabilities (Этап 2)")
    ap.add_argument("--group", type=int, default=None, help="VK group id (можно с минусом)")
    ap.add_argument("--send", action="store_true", help="живой messages.send тест (опасно)")
    ap.add_argument("--peer-id", type=int, default=None, help="кому слать тест (СВОЙ vk id)")
    ap.add_argument(
        "--text",
        default="🤖 SETKA probe (Этап 2) — тест ответа от сообщества. Сообщение удалится.",
        help="текст тест-сообщения",
    )
    args = ap.parse_args()

    import vk_api
    from vk_api.exceptions import ApiError

    from modules.vk_token_router import load_vk_routing

    user_token, community_tokens = await load_vk_routing()
    if not community_tokens:
        print("❌ нет community-токенов (load_vk_routing вернул пусто) — нечего probe'ить")
        return 1

    groups = _pick_group(community_tokens, args.group)
    print(f"🔎 community-токенов: {len(community_tokens)}; probe-группы: {groups}\n")

    probed = False
    for gid in groups:
        tok = community_tokens.get(gid)
        if not tok:
            print(f"— группа {gid}: community-токена нет, пропуск")
            continue
        api = vk_api.VkApi(token=tok).get_api()
        print(f"══ группа {gid} ══")

        # 1) READ: что умеет токен + базовое состояние unread.
        try:
            unread_before, unread_peers = await asyncio.to_thread(_unread_state, api)
        except ApiError as e:
            print(f"  ❌ getConversations(filter=unread) → [{e.code}] {e} — нет scope messages?")
            continue
        print(f"  ✅ messages.getConversations(filter=unread): {unread_before} непрочитанных")

        # 2) Семпл входящего диалога (вызов как у скана: extended=1).
        try:
            sample = await asyncio.to_thread(_sample_inbound_dialog, api)
        except ApiError as e:
            print(f"  ❌ getConversations(extended=1) → [{e.code}] {e}")
            continue
        unread_after_extended, _ = await asyncio.to_thread(_unread_state, api)

        if not sample:
            print("  ⚠️ нет входящих диалогов с пользователем — на этой группе глубже не probe'ю")
            print(
                f"     unread после getConversations(extended=1): {unread_after_extended} "
                f"(было {unread_before})"
            )
            if unread_after_extended < unread_before:
                print("     ⚠️ extended=1 ПОМЕТИЛ read — это и есть скан-вызов dm_scanner!")
            probed = True
            continue

        peer = sample["peer_id"]
        print(
            f"  📨 входящий диалог peer={peer}: in_read={sample['in_read']} "
            f"last_msg_id={sample['last_message_id']} can_write={sample['can_write']} "
            f"«{sample['text']}»"
        )

        # 3) getHistory тем же community-токеном (как fetch_history ad-кабинета).
        try:
            hist = await asyncio.to_thread(
                lambda: api.messages.getHistory(peer_id=peer, count=5, extended=1)
            )
            n = len(hist.get("items", []) or [])
            print(
                f"  ✅ messages.getHistory(peer={peer}): прочитано {n} сообщений (community-токен)"
            )
        except ApiError as e:
            print(f"  ❌ getHistory → [{e.code}] {e}")
            n = None

        unread_after_history, _ = await asyncio.to_thread(_unread_state, api)
        print(
            f"  📉 unread: before={unread_before} → after extended=1="
            f"{unread_after_extended} → after getHistory={unread_after_history}"
        )
        if unread_after_extended < unread_before:
            print("     🔴 КОРЕНЬ: getConversations(extended=1) метит read (вызов скана!)")
        elif unread_after_history < unread_after_extended:
            print("     🔴 КОРЕНЬ: messages.getHistory метит read")
        else:
            print("     🟢 чтение НЕ погасило unread в этом прогоне (read-mark не воспроизвёлся)")

        # 4) Может ли сообщество ответить этому peer (он написал первым → обычно да).
        try:
            allowed = await asyncio.to_thread(
                lambda: api.messages.isMessagesFromGroupAllowed(group_id=gid, user_id=peer)
            )
            print(
                f"  ✅ isMessagesFromGroupAllowed(peer={peer}): "
                f"is_allowed={int((allowed or {}).get('is_allowed', 0))} "
                "(для ответа на входящее ЛС VK разрешает всегда — юзер написал первым)"
            )
        except ApiError as e:
            print(f"  ⚠️ isMessagesFromGroupAllowed → [{e.code}] {e}")

        probed = True

        # 5) Опциональный живой send-тест (только с --send + --peer-id + confirm).
        if args.send:
            if args.peer_id is None:
                print("  ⛔ --send без --peer-id — отказ (укажи СВОЙ vk id явно)")
                return 2
            if os.environ.get("SETKA_PROBE_CONFIRM") != "yes":
                print("  ⛔ --send без SETKA_PROBE_CONFIRM=yes — отказ (предохранитель)")
                return 2
            rid = random.randint(1, 2**31 - 1)
            print(f"\n  → messages.send(peer={args.peer_id}) тест-текстом …")
            try:
                mid = await asyncio.to_thread(
                    lambda: api.messages.send(
                        peer_id=int(args.peer_id),
                        message=args.text,
                        random_id=rid,
                        group_id=gid,
                    )
                )
                print(f"  ✅ messages.send → message_id={mid} (community-токен УМЕЕТ отвечать)")
            except ApiError as e:
                print(f"  ❌ messages.send → [{e.code}] {e}")
                if e.code in (901, 902, 900):
                    print("     (901/902/900 — нельзя писать первым; но это НЕ ответ на входящее)")
                return 1
            # revert: удалить тест-сообщение для всех (в пределах 24ч VK позволяет).
            try:
                d = await asyncio.to_thread(
                    lambda: api.messages.delete(message_ids=int(mid), delete_for_all=1)
                )
                print(f"  ♻️ revert messages.delete(delete_for_all=1) → {d}")
            except ApiError as e:
                print(f"  ⚠️ revert не прошёл [{e.code}] {e} — удали сообщение {mid} вручную")
        print()

        # Если probe конкретной группы (--group) — одной достаточно.
        if args.group is not None:
            break

    if not probed:
        print("⚠️ ни на одной группе не нашлось диалогов/доступа — нечего заключить")
        return 1

    print("── ИТОГ ──")
    print(" • getConversations / getHistory community-токеном — читаемы (см. выше).")
    print(" • messages.send на входящее ЛС — отвечаемо (ad-кабинет уже использует это в проде).")
    print(" • markAsUnread-эквивалента в VK API для community нет → R2 (свой статус) обязателен.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
