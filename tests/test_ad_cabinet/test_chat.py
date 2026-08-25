"""Тесты чата клиент↔владелец (modules/ad_cabinet/chat).

Настоящий SQL (in-memory БД): unread-логика и изоляция тредов проверяются
запросами, а не мокками. Ключевое: чтение стороной X помечает прочитанными
сообщения стороны Y — и ТОЛЬКО их (счётчик другой стороны не трогается).
"""

from __future__ import annotations

import pytest

from database.models import AdClient
from modules.ad_cabinet import chat


async def _client(session, name="Клиент"):
    c = AdClient(name=name)
    session.add(c)
    await session.flush()
    return c


@pytest.mark.asyncio
async def test_post_and_fetch_roundtrip(db_session):
    c = await _client(db_session)
    await chat.post_message(db_session, c.id, chat.SENDER_CLIENT, "Здравствуйте!")
    await chat.post_message(db_session, c.id, chat.SENDER_OWNER, "Добрый день")
    await db_session.flush()

    rows = await chat.fetch_thread(db_session, c.id, reader=chat.SENDER_CLIENT)
    assert [r.sender for r in rows] == ["client", "owner"]


@pytest.mark.asyncio
async def test_validation(db_session):
    c = await _client(db_session)
    with pytest.raises(chat.ChatError):
        await chat.post_message(db_session, c.id, chat.SENDER_CLIENT, "   ")
    with pytest.raises(chat.ChatError):
        await chat.post_message(db_session, c.id, "stranger", "hi")
    with pytest.raises(chat.ChatError):
        await chat.post_message(db_session, c.id, chat.SENDER_CLIENT, "x" * (chat.BODY_MAX + 1))


@pytest.mark.asyncio
async def test_read_marking_is_one_sided(db_session):
    """Клиент прочитал тред → его счётчик обнулился, счётчик владельца ЦЕЛ."""
    c = await _client(db_session)
    await chat.post_message(db_session, c.id, chat.SENDER_CLIENT, "вопрос")
    await chat.post_message(db_session, c.id, chat.SENDER_OWNER, "ответ")
    await db_session.flush()

    assert await chat.unread_count(db_session, c.id, reader=chat.SENDER_CLIENT) == 1
    assert await chat.unread_count(db_session, c.id, reader=chat.SENDER_OWNER) == 1

    await chat.fetch_thread(db_session, c.id, reader=chat.SENDER_CLIENT)
    await db_session.flush()

    assert await chat.unread_count(db_session, c.id, reader=chat.SENDER_CLIENT) == 0
    # Входящее владельца («вопрос» клиента) осталось непрочитанным:
    assert await chat.unread_count(db_session, c.id, reader=chat.SENDER_OWNER) == 1


@pytest.mark.asyncio
async def test_after_id_polling_increment(db_session):
    c = await _client(db_session)
    first = await chat.post_message(db_session, c.id, chat.SENDER_OWNER, "раз")
    await db_session.flush()
    await chat.post_message(db_session, c.id, chat.SENDER_OWNER, "два")
    await db_session.flush()

    rows = await chat.fetch_thread(db_session, c.id, reader=chat.SENDER_CLIENT, after_id=first.id)
    assert [r.body for r in rows] == ["два"]


@pytest.mark.asyncio
async def test_thread_isolation(db_session):
    """Тред клиента А не видит сообщений клиента Б (настоящий WHERE)."""
    a = await _client(db_session, "А")
    b = await _client(db_session, "Б")
    await chat.post_message(db_session, a.id, chat.SENDER_CLIENT, "от А")
    await chat.post_message(db_session, b.id, chat.SENDER_CLIENT, "от Б")
    await db_session.flush()

    rows = await chat.fetch_thread(db_session, a.id, reader=chat.SENDER_OWNER)
    assert [r.body for r in rows] == ["от А"]


@pytest.mark.asyncio
async def test_owner_overview(db_session):
    a = await _client(db_session, "А")
    b = await _client(db_session, "Б")
    await chat.post_message(db_session, a.id, chat.SENDER_CLIENT, "непрочитанное")
    await chat.post_message(db_session, b.id, chat.SENDER_OWNER, "наше")
    await db_session.flush()

    threads = await chat.owner_overview(db_session)
    by_name = {t["client"]["name"]: t for t in threads}
    assert by_name["А"]["unread"] == 1
    assert by_name["Б"]["unread"] == 0
    assert by_name["А"]["last_message"]["body"] == "непрочитанное"
