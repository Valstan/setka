"""Список кабинетов клиентов (modules/ad_cabinet/cabinet_list).

Настоящий SQL (in-memory БД из conftest): сортировка по свежести и фильтр
«только с аккаунтом» проверяются запросами, не мокками. Главное, что здесь
охраняется: **номер кабинета = ad_clients.id** (второго нумератора нет),
**операторские правки не двигают строку** (иначе массовая операция в CRM
перетасует список и «кто последний шевелился» станет враньём).
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from database.models import AdChatMessage, AdClient, AdInteraction, AdPayment
from database.models_extended import RadarUser
from modules.ad_cabinet import cabinet_list

T0 = datetime(2026, 9, 1, 12, 0, 0)


async def _user(session, **kw):
    u = RadarUser(role="advertiser", **kw)
    session.add(u)
    await session.flush()
    return u


async def _client(session, *, name=None, radar_user_id=None, created_at=T0, **kw):
    c = AdClient(name=name, radar_user_id=radar_user_id, created_at=created_at, **kw)
    session.add(c)
    await session.flush()
    return c


@pytest.mark.asyncio
async def test_only_linked_clients_by_default(db_session):
    u = await _user(db_session, login="petrov")
    linked = await _client(db_session, name="Петров", radar_user_id=u.id)
    await _client(db_session, name="Из предложки")

    rows = await cabinet_list.list_cabinets(db_session)
    assert [r["id"] for r in rows] == [linked.id]

    rows_all = await cabinet_list.list_cabinets(db_session, include_unlinked=True)
    assert len(rows_all) == 2


@pytest.mark.asyncio
async def test_number_is_the_client_id(db_session):
    """Номер кабинета — первичный ключ карточки, тот же, что в ?as_client=."""
    u = await _user(db_session, login="a")
    c = await _client(db_session, name="А", radar_user_id=u.id)
    (row,) = await cabinet_list.list_cabinets(db_session)
    assert row["id"] == c.id


@pytest.mark.asyncio
async def test_sorted_by_latest_movement(db_session):
    """Чат, заказ клиента, оплата — движение; свежее сверху."""
    users = [await _user(db_session, login=f"u{i}") for i in range(3)]
    quiet = await _client(db_session, name="тихий", radar_user_id=users[0].id)
    chatty = await _client(db_session, name="писал", radar_user_id=users[1].id)
    payer = await _client(db_session, name="платил", radar_user_id=users[2].id)

    db_session.add(
        AdChatMessage(
            client_id=chatty.id, sender="client", body="?", created_at=T0 + timedelta(hours=1)
        )
    )
    db_session.add(
        AdPayment(client_id=payer.id, amount=500, status="paid", paid_at=T0 + timedelta(hours=2))
    )
    await db_session.flush()

    rows = await cabinet_list.list_cabinets(db_session)
    assert [r["id"] for r in rows] == [payer.id, chatty.id, quiet.id]
    assert rows[0]["last_activity_kind"] == "payment"
    assert rows[1]["last_activity_kind"] == "chat"
    assert rows[1]["unread"] == 1
    assert rows[2]["last_activity_kind"] == "created"
    assert rows[0]["paid_total"] == 500.0


@pytest.mark.asyncio
async def test_operator_edits_do_not_move_the_row(db_session):
    """Операторская запись журнала (actor=operator) — не движение кабинета."""
    u1 = await _user(db_session, login="x")
    u2 = await _user(db_session, login="y")
    old = await _client(db_session, name="старый", radar_user_id=u1.id, created_at=T0)
    new = await _client(
        db_session, name="новый", radar_user_id=u2.id, created_at=T0 + timedelta(minutes=5)
    )
    db_session.add(
        AdInteraction(
            client_id=old.id,
            kind="cancelled",
            actor="operator",
            created_at=T0 + timedelta(days=1),
        )
    )
    await db_session.flush()
    rows = await cabinet_list.list_cabinets(db_session)
    assert [r["id"] for r in rows] == [new.id, old.id]

    # А то же событие от клиента — движение.
    db_session.add(
        AdInteraction(
            client_id=old.id, kind="cancelled", actor="client", created_at=T0 + timedelta(days=1)
        )
    )
    await db_session.flush()
    rows = await cabinet_list.list_cabinets(db_session)
    assert rows[0]["id"] == old.id and rows[0]["last_activity_kind"] == "action"


@pytest.mark.asyncio
async def test_display_name_fallback_chain(db_session):
    u_dn = await _user(db_session, login="l1", display_name="Иван из ВК", vk_user_id=100)
    u_login = await _user(db_session, login="ivanov")
    u_vk = await _user(db_session, vk_user_id=777)
    c1 = await _client(db_session, name=None, radar_user_id=u_dn.id)
    c2 = await _client(db_session, name=None, radar_user_id=u_login.id)
    c3 = await _client(db_session, name=None, radar_user_id=u_vk.id)
    c4 = await _client(db_session, name="  ", radar_user_id=None)

    names = {
        r["id"]: r["name"]
        for r in await cabinet_list.list_cabinets(db_session, include_unlinked=True)
    }
    assert names[c1.id] == "Иван из ВК"
    assert names[c2.id] == "ivanov"
    assert names[c3.id] == "vk.com/id777"
    assert names[c4.id] == f"Кабинет №{c4.id}"
