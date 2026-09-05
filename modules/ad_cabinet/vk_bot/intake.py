"""Входящее бота: Bots Long Poll сообщества САРАФАН → диалог → ответы (этап 2).

Тот же механизм, что у Радиоточки (``modules/radar/vk_intake``): beat-тик раз в
минуту, короткий long-poll (10 с) внутри тика, ``ts`` в Redis. Отличие — здесь
у каждого собеседника есть состояние диалога (тоже Redis, ключ на ``peer_id``,
сутки), и обработчик — :mod:`.dialog`.

Всё сетевое инъектируется (``api_call``/``lp_get``/``sender``), поэтому тик
целиком гоняется в тестах без ВК и без Redis.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from modules.ad_cabinet.vk_bot import dialog, notify

logger = logging.getLogger(__name__)

TS_KEY = "setka:vkbot:lp_ts"
STATE_KEY = "setka:vkbot:state:{peer}"
STATE_TTL = 86400  # сутки: недописанный заказ не висит вечно

StateGet = Callable[[int], Optional[Dict[str, Any]]]
StateSet = Callable[[int, Optional[Dict[str, Any]]], None]


def extract_incoming(update: Dict[str, Any]) -> Optional[dialog.Incoming]:
    """Событие Long Poll → :class:`dialog.Incoming` или ``None``. Чистая.

    Только ``message_new`` от людей (положительный ``from_id``); сообщения
    самого сообщества и события чатов не наши.
    """
    if not isinstance(update, dict) or update.get("type") != "message_new":
        return None
    obj = update.get("object") or {}
    msg = obj.get("message") if isinstance(obj, dict) else None
    if not isinstance(msg, dict):
        return None
    from_id = msg.get("from_id")
    if not isinstance(from_id, int) or from_id <= 0:
        return None
    return dialog.Incoming(
        peer_id=from_id,
        text=(msg.get("text") or "").strip(),
        payload=dialog.parse_payload(msg.get("payload")),
        attachments=[a for a in (msg.get("attachments") or []) if isinstance(a, dict)],
    )


def redis_state_store(r) -> Tuple[StateGet, StateSet]:
    """Пара (get, set) поверх Redis-клиента с ``decode_responses``."""

    def get(peer_id: int) -> Optional[Dict[str, Any]]:
        raw = r.get(STATE_KEY.format(peer=peer_id))
        if not raw:
            return None
        try:
            v = json.loads(raw)
            return v if isinstance(v, dict) else None
        except ValueError:
            return None

    def set_(peer_id: int, state: Optional[Dict[str, Any]]) -> None:
        key = STATE_KEY.format(peer=peer_id)
        if state is None:
            r.delete(key)
        else:
            r.set(key, json.dumps(state, ensure_ascii=False, default=str), ex=STATE_TTL)

    return get, set_


def make_name_fetch(token: str, api_call) -> dialog.NameFetch:
    """``users.get`` community-токеном → «Имя Фамилия» для новой карточки."""

    async def fetch(vk_id: int) -> Optional[str]:
        resp = api_call(token, "users.get", user_ids=int(vk_id))
        rows = resp.get("response") if isinstance(resp, dict) else None
        if not rows:
            return None
        u = rows[0] or {}
        name = " ".join(x for x in (u.get("first_name"), u.get("last_name")) if x).strip()
        return name or None

    return fetch


def make_real_submitter() -> dialog.Submitter:
    """Заказ из бота — тем же ``submit_order``, что и кабинет.

    Для не-trusted клиента (все новые из бота) заказ ложится в ``pending`` без
    единого вызова ВК; фабрики нужны только trusted-клиентам.
    """

    async def submit(session, client, draft: Dict[str, Any]) -> Dict[str, Any]:
        from modules.ad_cabinet import client_orders
        from modules.vk_token_router import load_vk_routing
        from web.api.ad_crm import _cabinet_publisher_factory
        from web.api.advertiser_cabinet import _msk_to_unix, _real_attachment_builder

        user_token, _community_tokens = await load_vk_routing()
        publish_at = (
            datetime.fromisoformat(draft["publish_at"]) if draft.get("publish_at") else None
        )
        return await client_orders.submit_order(
            session,
            client=client,
            user_id=client.radar_user_id,
            text=draft.get("text") or "",
            image_paths=[],
            region_ids=list(draft.get("region_ids") or []),
            publish_at=publish_at,
            publish_now=bool(draft.get("publish_now")),
            publisher_factory=_cabinet_publisher_factory(session),
            attachment_builder=_real_attachment_builder(client.id, user_token),
            msk_to_unix=_msk_to_unix,
        )

    return submit


async def handle_one(
    incoming: dialog.Incoming,
    *,
    session_factory,
    state_get: StateGet,
    state_set: StateSet,
    submit: dialog.Submitter,
    name_fetch: Optional[dialog.NameFetch],
    sender: Callable[[int, str, Optional[str]], Awaitable[Dict[str, Any]]],
) -> List[str]:
    """Одно входящее: диалог в своей транзакции → ответы → пинг владельцу.

    Возвращает список событий (``chat``/``order``) — для лога тика.
    """
    async with session_factory() as session:
        replies, new_state, events = await dialog.handle(
            session,
            incoming,
            state_get(incoming.peer_id),
            submit=submit,
            name_fetch=name_fetch,
        )
        await session.commit()
        client = await dialog.find_client(session, incoming.peer_id)
    state_set(incoming.peer_id, new_state)

    for text, kb in replies:
        ok, code = notify.send_outcome(await sender(incoming.peer_id, text, kb))
        if not ok:
            logger.warning("vk_bot reply to %s failed: vk error %s", incoming.peer_id, code)

    label = f"№{client.id} «{client.name or client.id}»" if client else f"vk id {incoming.peer_id}"
    for ev in events:
        if ev == "chat":
            preview = (incoming.text or "").strip().replace("\n", " ")[:80]
            await notify.notify_owner(
                f"💬 ВК-бот: клиент {label}: {preview} — ответить в /ad → Кабинеты",
                dedup_key=f"chat:{client.id if client else incoming.peer_id}",
                dedup_ttl=600,
            )
        elif ev == "order":
            await notify.notify_owner(
                f"🛎 ВК-бот: клиент {label} сделал заказ — ждёт одобрения в /ad"
            )
        elif ev == "order_direct":
            await notify.notify_owner(
                f"🛎 ВК-бот: клиент {label} (trusted) сделал заказ — уже в VK-отложке, "
                "отменить можно в /ad → Кабинеты"
            )
        elif ev == "signup":
            await notify.notify_owner(
                f"🆕 ВК-бот: новый клиент {label} — карточка заведена автоматически",
                dedup_key=f"vkbot_signup:{incoming.peer_id}",
            )
    return events


async def poll_once(
    *,
    token: str,
    group_id: int,
    session_factory,
    state_get: StateGet,
    state_set: StateSet,
    ts_get: Callable[[], Optional[str]],
    ts_set: Callable[[Optional[str]], None],
    submit: Optional[dialog.Submitter] = None,
    api_call=None,
    lp_get=None,
    sender=None,
) -> Dict[str, Any]:
    """Один тик: getLongPollServer → a_check → диалоги → ts."""
    from modules.radar.vk_intake import lp_fetch, vk_api_call

    api_call = api_call or vk_api_call
    lp_get = lp_get or lp_fetch
    submit = submit or make_real_submitter()
    sender = sender or notify._make_sender(token, group_id)
    name_fetch = make_name_fetch(token, api_call)

    srv = api_call(token, "groups.getLongPollServer", group_id=group_id)
    resp = srv.get("response") if isinstance(srv, dict) else None
    if not resp or not resp.get("server"):
        err = (srv or {}).get("error") or {}
        return {"ok": False, "error": err.get("error_msg", "getLongPollServer failed")}

    server, key, fresh_ts = resp["server"], resp["key"], resp["ts"]
    ts = ts_get() or fresh_ts
    data = lp_get(server, key, ts)
    if "failed" in data:
        if data.get("failed") == 1 and data.get("ts") is not None:
            ts_set(str(data["ts"]))
        else:
            ts_set(None)
        return {"ok": True, "reinit": data.get("failed"), "processed": 0}

    updates = data.get("updates") or []
    processed, events_total = 0, 0
    for upd in updates:
        inc = extract_incoming(upd)
        if inc is None:
            continue
        processed += 1
        try:
            events = await handle_one(
                inc,
                session_factory=session_factory,
                state_get=state_get,
                state_set=state_set,
                submit=submit,
                name_fetch=name_fetch,
                sender=sender,
            )
            events_total += len(events)
        except Exception:  # noqa: BLE001 - один битый диалог не валит остальные
            logger.exception("vk_bot: handle_one failed for peer %s", inc.peer_id)
            try:
                await sender(
                    inc.peer_id,
                    "Что-то пошло не так, попробуйте ещё раз или напишите владельцу.",
                    dialog.MAIN_KEYBOARD,
                )
            except Exception:  # noqa: BLE001
                pass

    if data.get("ts") is not None:
        ts_set(str(data["ts"]))
    return {"ok": True, "updates": len(updates), "processed": processed, "events": events_total}


__all__ = ["extract_incoming", "redis_state_store", "poll_once", "handle_one", "TS_KEY"]
