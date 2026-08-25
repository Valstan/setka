"""Чат клиент↔владелец в кабинете рекламодателя (таблица ``ad_chat_messages``).

In-app канал: работает и для клиентов без VK (ЕСА-вход по паролю). VK-тред
(``client_thread``/``client_reply``) остаётся вторым каналом для VK-клиентов.

Unread-семантика: ``read_at IS NULL`` = не прочитано ПРОТИВОПОЛОЖНОЙ стороной.
Чтение треда стороной X помечает прочитанными сообщения стороны Y — и только
их: собственные сообщения читателя счётчик другой стороны не трогают.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select

from database.models import AdChatMessage, AdClient

logger = logging.getLogger(__name__)

BODY_MAX = 2000
FETCH_LIMIT = 50

SENDER_CLIENT = "client"
SENDER_OWNER = "owner"
_VALID_SENDERS = {SENDER_CLIENT, SENDER_OWNER}


class ChatError(ValueError):
    """Ошибка валидации сообщения — текст безопасен для показа."""


async def post_message(session, client_id: int, sender: str, body: str) -> AdChatMessage:
    """Отправить сообщение в тред клиента. Commit — на вызывающем."""
    if sender not in _VALID_SENDERS:
        raise ChatError(f"Неизвестный отправитель: {sender}")
    body = (body or "").strip()
    if not body:
        raise ChatError("Пустое сообщение")
    if len(body) > BODY_MAX:
        raise ChatError(f"Сообщение длиннее лимита {BODY_MAX} символов")
    row = AdChatMessage(client_id=client_id, sender=sender, body=body)
    session.add(row)
    return row


async def fetch_thread(
    session,
    client_id: int,
    *,
    reader: str,
    after_id: Optional[int] = None,
    limit: int = FETCH_LIMIT,
) -> List[AdChatMessage]:
    """Тред клиента (по возрастанию id) + отметка прочтения читателем.

    ``after_id`` — polling-инкремент: отдаются только сообщения новее. Отметка
    прочтения ставится сообщениям ПРОТИВОПОЛОЖНОЙ стороны без ``read_at`` —
    по всему треду, не только по отданной странице (открытый тред прочитан).
    """
    if reader not in _VALID_SENDERS:
        raise ChatError(f"Неизвестный читатель: {reader}")
    stmt = (
        select(AdChatMessage)
        .where(AdChatMessage.client_id == client_id)
        .order_by(AdChatMessage.id.asc())
    )
    if after_id:
        stmt = stmt.where(AdChatMessage.id > int(after_id))
    rows = list((await session.execute(stmt.limit(limit))).scalars().all())

    other = SENDER_OWNER if reader == SENDER_CLIENT else SENDER_CLIENT
    unread = (
        await session.execute(
            select(AdChatMessage).where(
                AdChatMessage.client_id == client_id,
                AdChatMessage.sender == other,
                AdChatMessage.read_at.is_(None),
            )
        )
    ).scalars()
    now = datetime.utcnow()
    for msg in unread:
        msg.read_at = now
    return rows


async def unread_count(session, client_id: int, *, reader: str) -> int:
    """Сколько сообщений другой стороны не прочитано читателем."""
    other = SENDER_OWNER if reader == SENDER_CLIENT else SENDER_CLIENT
    return (
        await session.execute(
            select(func.count())
            .select_from(AdChatMessage)
            .where(
                AdChatMessage.client_id == client_id,
                AdChatMessage.sender == other,
                AdChatMessage.read_at.is_(None),
            )
        )
    ).scalar_one()


async def owner_overview(session) -> List[Dict[str, Any]]:
    """Список тредов для владельца: клиент, последнее сообщение, unread.

    Один проход по агрегатам (без N+1): последняя строка каждого треда +
    счётчик непрочитанных входящих (``sender='client'``, ``read_at IS NULL``).
    Сортировка — по свежести переписки.
    """
    last_ids = (
        select(
            AdChatMessage.client_id.label("cid"),
            func.max(AdChatMessage.id).label("last_id"),
        )
        .group_by(AdChatMessage.client_id)
        .subquery()
    )
    unread_sq = (
        select(
            AdChatMessage.client_id.label("cid"),
            func.count().label("unread"),
        )
        .where(AdChatMessage.sender == SENDER_CLIENT, AdChatMessage.read_at.is_(None))
        .group_by(AdChatMessage.client_id)
        .subquery()
    )
    rows = (
        await session.execute(
            select(
                AdClient,
                AdChatMessage,
                func.coalesce(unread_sq.c.unread, 0),
            )
            .join(last_ids, last_ids.c.cid == AdClient.id)
            .join(AdChatMessage, AdChatMessage.id == last_ids.c.last_id)
            .outerjoin(unread_sq, unread_sq.c.cid == AdClient.id)
            .order_by(AdChatMessage.id.desc())
        )
    ).all()
    return [
        {
            "client": client.to_dict(),
            "last_message": msg.to_dict(),
            "unread": int(unread),
        }
        for client, msg, unread in rows
    ]
