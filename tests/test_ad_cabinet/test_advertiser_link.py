"""Тесты линковки «аккаунт ЕСА → карточка клиента» (advertiser_link).

Изоляционные утверждения гоняются настоящим SQL (in-memory БД из conftest):
фейковая сессия не умеет краснеть на неверном WHERE.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from database.models import AdClient
from database.models_extended import RadarUser
from modules.ad_cabinet import advertiser_link


async def _user(session, *, login=None, vk_user_id=None, role="radar"):
    u = RadarUser(login=login, role=role, vk_user_id=vk_user_id)
    session.add(u)
    await session.flush()
    return u


@pytest.mark.asyncio
async def test_resolve_by_explicit_fk(db_session):
    user = await _user(db_session, login="client1")
    card = AdClient(radar_user_id=user.id, name="Явная")
    db_session.add(card)
    await db_session.flush()

    got = await advertiser_link.resolve_client(db_session, user)
    assert got is not None and got.id == card.id


@pytest.mark.asyncio
async def test_resolve_fallback_by_vk_id_backfills_fk(db_session):
    """Карточка, которую оператор давно ведёт по VK id, достаётся владельцу
    этого VK id — и FK дозаписывается (self-healing, одноразово)."""
    user = await _user(db_session, vk_user_id=555)
    card = AdClient(author_vk_id=555, name="Из предложки")
    db_session.add(card)
    await db_session.flush()

    got = await advertiser_link.resolve_client(db_session, user)
    assert got is not None and got.id == card.id
    assert got.radar_user_id == user.id  # бэкфилл


@pytest.mark.asyncio
async def test_fallback_does_not_steal_linked_card(db_session):
    """Карточка с уже записанным FK НЕ достаётся другому юзеру с тем же VK id —
    проверка умеет краснеть: без ``radar_user_id IS NULL`` в WHERE тест падает."""
    owner = await _user(db_session, vk_user_id=777)
    intruder = await _user(db_session, vk_user_id=777)
    card = AdClient(author_vk_id=777, radar_user_id=owner.id, name="Занята")
    db_session.add(card)
    await db_session.flush()

    got = await advertiser_link.resolve_client(db_session, intruder)
    assert got is None


@pytest.mark.asyncio
async def test_no_link_by_manual_fields(db_session):
    """Совпадение имени/телефона карточку НЕ отдаёт — линковка только по identity."""
    user = await _user(db_session, login="pretender")
    db_session.add(AdClient(name="pretender", phone="+7 900 000-00-00"))
    await db_session.flush()

    assert await advertiser_link.resolve_client(db_session, user) is None


@pytest.mark.asyncio
async def test_onboard_creates_untrusted_card(db_session):
    user = await _user(db_session, vk_user_id=901)
    card = await advertiser_link.onboard_client(db_session, user, name="Новый", phone="+7 1")
    await db_session.flush()

    assert card.radar_user_id == user.id
    assert card.author_vk_id == 901
    assert card.trusted is False  # модерация новых клиентов
    assert card.stage == advertiser_link.ONBOARD_STAGE


@pytest.mark.asyncio
async def test_onboard_is_idempotent(db_session):
    """Повторный онбординг возвращает ту же карточку, а не плодит вторую."""
    user = await _user(db_session, login="again")
    first = await advertiser_link.onboard_client(db_session, user)
    await db_session.flush()
    second = await advertiser_link.onboard_client(db_session, user)
    await db_session.flush()

    assert first.id == second.id
    total = len((await db_session.execute(select(AdClient))).scalars().all())
    assert total == 1
