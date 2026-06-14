"""Intake-бот радара («Карман»): форвард поста канала боту → канал в радар.

Принимающего Telegram-бота в проекте не было — поднимаем приёмник через
**getUpdates-polling** (`api.telegram.org` с VPS доступен, в отличие от t.me —
поэтому источники радара ходят через relay, а бот-API напрямую). Beat
`poll_radar_bot` тикает раз в минуту: забирает новые апдейты с сохранённым offset
(redis), и на КАЖДЫЙ пересланный боту пост из канала резолвит канал → добавляет
radar-источник (tg) + подписку оператора → отвечает в чат.

Гейт #008: работает только если задан `RADAR_BOT_NAME` (ключ в `TELEGRAM_TOKENS`)
и непустой allowlist `RADAR_BOT_ALLOWED_USERS`. Неавторизованному — ответ с его
telegram-id (чтобы владелец узнал свой id и внёс в allowlist).

Чистые функции (extract_forwarded_channel/build_reply) — тестируемы без сети/БД;
сетевые/БД-зависимости в poll_radar_bot_once инъектируются.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

TG_API = "https://api.telegram.org"
GETUPDATES_TIMEOUT = 0  # короткий poll внутри beat-тика (не long-poll)


def extract_forwarded_channel(message: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """Из telegram-сообщения вытащить (username, title) исходного КАНАЛА форварда.

    Поддерживает `forward_origin` (Bot API 7+) и `forward_from_chat` (legacy). Чистая.
    username=None, если это не форвард-из-канала ИЛИ у канала нет публичного username
    (приватный канал → не мониторится через t.me/s/, но title вернём для сообщения)."""
    if not isinstance(message, dict):
        return None, None
    chat: Optional[Dict[str, Any]] = None
    origin = message.get("forward_origin")
    if isinstance(origin, dict) and origin.get("type") == "channel":
        c = origin.get("chat")
        if isinstance(c, dict):
            chat = c
    if chat is None:
        ffc = message.get("forward_from_chat")
        if isinstance(ffc, dict) and ffc.get("type") == "channel":
            chat = ffc
    if chat is None:
        return None, None
    return chat.get("username"), chat.get("title")


def build_reply(status: str, *, username: str = "", title: str = "", detail: str = "") -> str:
    """Текст ответа бота по исходу обработки. Чистая."""
    if status == "added":
        return f"✅ Канал @{username} ({title}) добавлен в радар — буду мониторить."
    if status == "exists":
        return f"ℹ️ Канал @{username} уже в радаре."
    if status == "private":
        name = title or "канал"
        return (
            f"⚠️ «{name}» без публичного username (приватный) — мониторить через web-превью нельзя."
        )
    if status == "not_forward":
        return "Перешлите боту пост из канала — добавлю его в радар. (Обычное сообщение не канал.)"
    if status == "unauthorized":
        return (
            f"🔒 Нет доступа. Ваш Telegram-id: {detail}. "
            "Добавьте его в RADAR_BOT_ALLOWED_USERS, чтобы добавлять каналы."
        )
    if status == "error":
        return f"❌ Не удалось добавить канал: {detail}"
    return ""


async def handle_message(
    message: Dict[str, Any],
    *,
    allowed_users: set,
    add_channel: Callable[[str], Any],
) -> Optional[Tuple[int, str]]:
    """Обработать одно входящее сообщение → (chat_id, reply_text) или None (молчим).

    `add_channel(username)` — async callable, добавляет канал в радар и возвращает
    {"status": "added"|"exists"|"error", "title": str, "error": str}."""
    if not isinstance(message, dict):
        return None
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    if chat_id is None:
        return None
    from_user = message.get("from") or {}
    from_id = from_user.get("id")

    # Авторизация: только allowlist. Чужому — вернуть его id (для самообнаружения).
    if from_id not in allowed_users:
        return chat_id, build_reply("unauthorized", detail=str(from_id))

    username, title = extract_forwarded_channel(message)
    # Не форвард из канала вообще?
    if username is None and title is None:
        return chat_id, build_reply("not_forward")
    # Канал-форвард, но приватный (нет username) → мониторить нельзя.
    if not username:
        return chat_id, build_reply("private", title=title or "")

    res = await add_channel(username)
    status = res.get("status", "error")
    return chat_id, build_reply(
        status,
        username=username,
        title=res.get("title") or title or username,
        detail=res.get("error", ""),
    )


def _tg_call(token: str, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Синхронный вызов Bot API (httpx). Возвращает распарсенный JSON или {}."""
    try:
        r = httpx.get(f"{TG_API}/bot{token}/{method}", params=params, timeout=20)
        return r.json()
    except Exception as e:  # noqa: BLE001 - сеть; не валим beat
        logger.warning("radar-bot %s failed: %s", method, e)
        return {}


async def poll_radar_bot_once(
    *,
    token: str,
    allowed_users: set,
    add_channel: Callable[[str], Any],
    offset_get: Callable[[], Optional[int]],
    offset_set: Callable[[int], None],
    call: Optional[Callable[[str, str, Dict[str, Any]], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Один тик приёмника: getUpdates → обработать → ответить → продвинуть offset.

    `call(token, method, params)` инъектируется (тест/прод); по умолчанию — Bot API."""
    call = call or _tg_call
    offset = offset_get()
    params: Dict[str, Any] = {"timeout": GETUPDATES_TIMEOUT, "allowed_updates": '["message"]'}
    if offset is not None:
        params["offset"] = offset
    resp = call(token, "getUpdates", params)
    if not resp.get("ok"):
        return {"ok": False, "processed": 0, "error": resp.get("description", "getUpdates failed")}

    updates = resp.get("result") or []
    processed, added, replied = 0, 0, 0
    max_update_id = None
    for upd in updates:
        max_update_id = upd.get("update_id")
        message = upd.get("message")
        if not message:
            continue
        processed += 1
        try:
            out = await handle_message(
                message, allowed_users=allowed_users, add_channel=add_channel
            )
        except Exception:  # noqa: BLE001 - один битый апдейт не валит остальные
            logger.exception("radar-bot: handle_message failed")
            continue
        if out is None:
            continue
        chat_id, reply = out
        if reply.startswith("✅"):
            added += 1
        call(token, "sendMessage", {"chat_id": chat_id, "text": reply})
        replied += 1

    if max_update_id is not None:
        offset_set(max_update_id + 1)
    return {
        "ok": True,
        "updates": len(updates),
        "processed": processed,
        "added": added,
        "replied": replied,
    }
