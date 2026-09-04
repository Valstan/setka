"""Счётчики и баланс (Этап 1, PR 1.4 аудита кабинета 2026-09-05) — настоящая БД.

- «заказано» не считает отклонённые/упавшие; клиент из ВК-бота (без аккаунта,
  но с собственными действиями) виден в списке по умолчанию;
- деньги пакета попадают в ad_payments один раз и видны в «оплачено»;
- свежесть строки: подтверждение оплаты и выход поста двигают, ответ владельца в
  чате и визит — нет;
- перерасход не считает публикации в счёт пакета (price=0).
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from database.models import (
    AdChatMessage,
    AdClient,
    AdClientPackage,
    AdInteraction,
    AdPayment,
    AdPublication,
    AdScheduledPost,
)
from database.models_extended import RadarUser
from modules.ad_cabinet import packages, spend_balance
from modules.ad_cabinet.cabinet_list import list_cabinets

T = datetime(2026, 9, 5, 12, 0)


def _post(client_id, status, day=0, gid=-100):
    return AdScheduledPost(
        client_id=client_id,
        community_vk_id=gid,
        text="t",
        publish_date=T + timedelta(days=day),
        status=status,
        price=350,
    )


@pytest.mark.asyncio
async def test_ordered_excludes_rejected_and_failed(db_session):
    db_session.add(RadarUser(id=5, login="c", role="advertiser"))
    db_session.add(AdClient(id=1, author_vk_id=7, name="К", radar_user_id=5))
    db_session.add_all(
        [
            _post(1, "pending", 0),
            _post(1, "scheduled", 1),
            _post(1, "rejected", 2),
            _post(1, "failed", 3),
            _post(1, "cancelled", 4),
        ]
    )
    await db_session.commit()
    rows = await list_cabinets(db_session)
    assert rows[0]["posts_ordered"] == 2


@pytest.mark.asyncio
async def test_bot_client_without_account_is_listed_by_default(db_session):
    db_session.add(AdClient(id=1, author_vk_id=7, name="Бот-клиент"))
    db_session.add(AdClient(id=2, author_vk_id=8, name="Тихий из предложки"))
    db_session.add(
        AdInteraction(client_id=1, kind="cabinet_signup", actor="client", summary="из бота")
    )
    await db_session.commit()
    rows = await list_cabinets(db_session)
    assert [r["id"] for r in rows] == [1]
    rows_all = await list_cabinets(db_session, include_unlinked=True)
    assert {r["id"] for r in rows_all} == {1, 2}


@pytest.mark.asyncio
async def test_package_payment_recorded_once_and_counted(db_session):
    db_session.add(RadarUser(id=5, login="c", role="advertiser"))
    db_session.add(AdClient(id=1, author_vk_id=7, name="К", radar_user_id=5))
    pkg = AdClientPackage(
        id=10, client_id=1, kind="prepaid", posts_total=5, price=1500, paid_at=T, is_active=True
    )
    promo = AdClientPackage(
        id=11, client_id=1, kind="free_promo", posts_total=3, price=0, paid_at=T, is_active=True
    )
    db_session.add_all([pkg, promo])
    await db_session.flush()
    pay = await packages.record_package_payment(db_session, pkg)
    again = await packages.record_package_payment(db_session, pkg)
    assert pay is not None and again is pay
    assert await packages.record_package_payment(db_session, promo) is None
    await db_session.commit()
    pays = (await db_session.execute(select(AdPayment))).scalars().all()
    assert len(pays) == 1
    assert pays[0].provider == "package" and pays[0].external_id == "10"
    assert pays[0].status == "paid" and pays[0].units_paid == 5
    rows = await list_cabinets(db_session)
    assert rows[0]["paid_total"] == 1500.0
    assert rows[0]["last_activity_kind"] == "payment"


@pytest.mark.asyncio
async def test_freshness_moves_on_confirmation_and_publish_not_on_owner_chat(db_session):
    db_session.add(RadarUser(id=5, login="c", role="advertiser"))
    db_session.add(AdClient(id=1, author_vk_id=7, name="А", radar_user_id=5, created_at=T))
    db_session.add(AdClient(id=2, author_vk_id=8, name="Б", radar_user_id=5, created_at=T))
    # у А — платёж создан давно, подтверждён только что; у Б — ответ владельца в чате
    db_session.add(
        AdPayment(
            client_id=1,
            amount=350,
            status="paid",
            paid_at=T - timedelta(days=3),
            paid_confirmed_at=T + timedelta(hours=1),
        )
    )
    db_session.add(
        AdChatMessage(client_id=2, sender="owner", body="ответ", created_at=T + timedelta(hours=2))
    )
    await db_session.commit()
    rows = await list_cabinets(db_session)
    assert rows[0]["id"] == 1 and rows[0]["last_activity_kind"] == "payment"
    assert rows[0]["last_activity_at"].startswith((T + timedelta(hours=1)).isoformat()[:16])
    # выход поста двигает Б наверх
    db_session.add(
        AdPublication(
            client_id=2,
            community_vk_id=-100,
            status="published",
            published_at=T + timedelta(hours=3),
        )
    )
    await db_session.commit()
    rows = await list_cabinets(db_session)
    assert rows[0]["id"] == 2 and rows[0]["last_activity_kind"] == "published"


@pytest.mark.asyncio
async def test_overspend_ignores_package_publications(db_session):
    db_session.add(AdClient(id=1, author_vk_id=7, name="К"))
    db_session.add(AdPayment(client_id=1, amount=700, status="paid", units_paid=2, paid_at=T))
    db_session.add_all(
        [
            AdPublication(client_id=1, community_vk_id=-100, status="published", price=350),
            AdPublication(client_id=1, community_vk_id=-101, status="published", price=350),
            AdPublication(client_id=1, community_vk_id=-102, status="published", price=0),
            AdPublication(client_id=1, community_vk_id=-103, status="published", price=0),
        ]
    )
    await db_session.commit()
    over = await spend_balance.collect_overspent(db_session, now=T)
    assert over == []
    db_session.add(AdPublication(client_id=1, community_vk_id=-104, status="published", price=350))
    await db_session.commit()
    over = await spend_balance.collect_overspent(db_session, now=T)
    assert len(over) == 1 and over[0]["consumed"] == 3 and over[0]["over"] == 1
