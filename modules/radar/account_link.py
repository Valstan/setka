"""Привязка личных аккаунтов пользователя к радару («Радиоточка»).

Пользователь подключает свою личку (сейчас — Telegram), чтобы радар присылал туда
найденные новости. Поток без OAuth и без хранения пользовательских токенов:

1. В кабинете жмёт «Подключить Telegram» → web генерит одноразовый КОД и даёт
   deep-link `https://t.me/<bot>?start=<код>`.
2. Пользователь открывает бота и жмёт Start (Telegram шлёт боту `/start <код>`).
3. Intake-бот (`bot_intake`) на `/start <код>` резолвит код → создаёт telegram-вывод
   (`radar_outputs`) с его ``chat_id`` → радар шлёт новости ему в личку.

Код живёт в Redis с TTL (короткоживущий, одноразовый) — миграция не нужна. Код сам
авторизует привязку (его знает только владелец кабинета), поэтому бот обрабатывает
``/start <код>`` в обход allowlist (allowlist — только для форвард-интейка каналов).
"""

from __future__ import annotations

import logging
import secrets
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# Без похожих символов (0/O, 1/I/L) — код диктуется/набирается руками.
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_CODE_LEN = 6
_CODE_TTL_SECONDS = 15 * 60
_CODE_PREFIX = "setka:radar_linkcode:"
_BOT_USERNAME_KEY = "setka:radar_bot_username"
_BOT_USERNAME_TTL = 24 * 3600


def _redis():
    from modules.digest_heartbeat import _redis as _r

    return _r()


def generate_link_code(user_id: int, channel: str = "telegram") -> Optional[str]:
    """Сгенерировать одноразовый код привязки и положить в Redis (TTL 15 мин).

    Возвращает код или None, если Redis недоступен.
    """
    client = _redis()
    if client is None:
        return None
    code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LEN))
    client.setex(f"{_CODE_PREFIX}{code}", _CODE_TTL_SECONDS, f"{channel}:{int(user_id)}")
    return code


def resolve_link_code(code: str, *, consume: bool = True) -> Optional[Tuple[str, int]]:
    """Код → (channel, user_id) или None (не найден/истёк). По умолчанию потребляет."""
    client = _redis()
    if client is None or not code:
        return None
    key = f"{_CODE_PREFIX}{code.strip().upper()}"
    raw = client.get(key)
    if raw is None:
        return None
    if consume:
        client.delete(key)
    value = raw.decode() if isinstance(raw, (bytes, bytearray)) else str(raw)
    channel, _, uid = value.partition(":")
    try:
        return channel, int(uid)
    except (TypeError, ValueError):
        return None


async def link_telegram(
    code: str,
    chat_id: int,
    *,
    display_name: str = "",
    bot_name: str = "",
    session_factory=None,
) -> dict:
    """Привязать Telegram-чат к пользователю по коду → создать telegram-вывод.

    Возвращает {status: linked|exists|invalid}. Идемпотентно: повторный `/start`
    тем же чатом того же юзера = exists, дубля вывода не делает. Курсор доставки
    стартует с текущего MAX(item.id) — старого бэклога новый вывод не шлёт.
    """
    resolved = resolve_link_code(code)
    if resolved is None:
        return {"status": "invalid"}
    channel, user_id = resolved
    if channel != "telegram":
        return {"status": "invalid"}

    from sqlalchemy import select

    from database import models  # noqa: F401 - конфигурация мапперов (PR #189)
    from database.connection import AsyncSessionLocal
    from database.models_extended import RadarOutput
    from modules.radar.delivery import max_item_id

    if session_factory is None:
        session_factory = AsyncSessionLocal

    target = str(int(chat_id))
    async with session_factory() as session:
        existing = (
            await session.execute(
                select(RadarOutput).where(
                    RadarOutput.user_id == user_id,
                    RadarOutput.type == "telegram",
                    RadarOutput.target == target,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            # Реактивируем, если был выключен — повторная привязка = «включить снова».
            if not existing.is_active:
                existing.is_active = True
                await session.commit()
            return {"status": "exists", "output_id": existing.id}

        cursor = await max_item_id(session)
        title = "Telegram"
        if display_name:
            title = f"Telegram · {display_name}"[:200]
        output = RadarOutput(
            user_id=user_id,
            type="telegram",
            title=title,
            target=target,
            mode="excerpt_link",
            config={"bot_name": bot_name.strip().upper()} if bot_name else None,
            last_item_id=cursor,
        )
        session.add(output)
        await session.commit()
        await session.refresh(output)
        return {"status": "linked", "output_id": output.id}


def get_bot_username(token: str) -> Optional[str]:
    """@username бота через getMe (кэш в Redis). None при сбое — UI даст fallback."""
    client = _redis()
    if client is not None:
        cached = client.get(_BOT_USERNAME_KEY)
        if cached:
            return cached.decode() if isinstance(cached, (bytes, bytearray)) else str(cached)
    if not token:
        return None
    try:
        import httpx

        resp = httpx.get(f"https://api.telegram.org/bot{token}/getMe", timeout=15)
        data = resp.json()
        username = ((data or {}).get("result") or {}).get("username")
    except Exception as e:  # noqa: BLE001 - сеть; UI переживёт отсутствие username
        logger.warning("radar link: getMe failed: %s", e)
        return None
    if username and client is not None:
        client.setex(_BOT_USERNAME_KEY, _BOT_USERNAME_TTL, username)
    return username
