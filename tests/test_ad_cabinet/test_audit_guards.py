"""Гарантии аудита кабинета 2026-09-05 (миграция 096) на настоящей БД.

- уникумы: вторая публикация / второй awaiting по одной отложке, второй активный
  пост клиента в то же сообщество в тот же день — IntegrityError, а не тихий дубль;
- архив клиента: карточка уходит из списка кабинетов, но остаётся в БД;
- очередь модерации показывает pending-сироту без клиента (OUTER JOIN).
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from database.models import AdClient, AdPayment, AdPublication, AdScheduledPost
from modules.ad_cabinet.cabinet_list import list_cabinets


def _sched(**kw):
    defaults = dict(
        community_vk_id=-100,
        text="t",
        publish_date=datetime(2026, 9, 10, 10, 0),
        status="scheduled",
        client_id=1,
        price=350,
    )
    defaults.update(kw)
    return AdScheduledPost(**defaults)


@pytest.mark.asyncio
async def test_second_publication_for_same_post_is_rejected(db_session):
    db_session.add(AdClient(id=1, author_vk_id=7, name="К"))
    row = _sched()
    db_session.add(row)
    await db_session.flush()
    db_session.add(AdPublication(client_id=1, community_vk_id=-100, scheduled_post_id=row.id))
    await db_session.commit()
    db_session.add(AdPublication(client_id=1, community_vk_id=-100, scheduled_post_id=row.id))
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_second_awaiting_payment_for_same_post_is_rejected(db_session):
    db_session.add(AdClient(id=1, author_vk_id=7, name="К"))
    row = _sched()
    db_session.add(row)
    await db_session.flush()
    db_session.add(AdPayment(client_id=1, amount=350, status="awaiting", scheduled_post_id=row.id))
    await db_session.commit()
    # paid по той же отложке — можно (оператор мог записать оплату руками)
    db_session.add(AdPayment(client_id=1, amount=350, status="paid", scheduled_post_id=row.id))
    await db_session.commit()
    db_session.add(AdPayment(client_id=1, amount=350, status="awaiting", scheduled_post_id=row.id))
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_second_active_post_same_client_community_day_is_rejected(db_session):
    db_session.add(AdClient(id=1, author_vk_id=7, name="К"))
    db_session.add(_sched(publish_date=datetime(2026, 9, 10, 10, 0)))
    await db_session.commit()
    # другой день — ок; отменённый в тот же день — ок
    db_session.add(_sched(publish_date=datetime(2026, 9, 11, 10, 0)))
    db_session.add(_sched(publish_date=datetime(2026, 9, 10, 18, 0), status="cancelled"))
    await db_session.commit()
    db_session.add(_sched(publish_date=datetime(2026, 9, 10, 18, 0)))
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_archived_client_hidden_from_cabinet_list_but_kept(db_session):
    from database.models_extended import RadarUser

    db_session.add(RadarUser(id=5, login="c1", role="advertiser"))
    db_session.add(AdClient(id=1, author_vk_id=7, name="Живой", radar_user_id=5))
    db_session.add(AdClient(id=2, author_vk_id=8, name="Архив", radar_user_id=5, is_archived=True))
    await db_session.commit()
    rows = await list_cabinets(db_session, include_unlinked=True)
    assert [r["id"] for r in rows] == [1]
    kept = (await db_session.execute(select(AdClient).where(AdClient.id == 2))).scalar_one()
    assert kept.is_archived is True


async def test_moderation_queue_shows_orphan_pending():
    from web.api import ad_crm

    orphan = _sched(status="pending", client_id=None)
    orphan.id = 9
    db = AsyncMock()
    r = MagicMock()
    r.all.return_value = [(orphan, None)]
    db.execute = AsyncMock(return_value=r)
    out = await ad_crm.moderation_queue(db=db)
    assert out["pending"][0]["id"] == 9 and out["pending"][0]["client"] is None


async def test_delete_client_archives_instead_of_deleting():
    from web.api import ad_crm

    client = AdClient(id=3, author_vk_id=1, name="К")
    db = AsyncMock()
    db.get = AsyncMock(return_value=client)
    db.add = MagicMock()
    db.delete = AsyncMock()
    out = await ad_crm.delete_client(3, db=db)
    assert out == {"success": True, "archived": True}
    assert client.is_archived is True
    db.delete.assert_not_awaited()
    out = await ad_crm.unarchive_client(3, db=db)
    assert client.is_archived is False and out["archived"] is False
