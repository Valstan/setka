"""Тесты CRM рекламного кабинета (web/api/ad_crm) — блок C.

Сессия БД мокается (AsyncMock): без реальной БД, в стиле
tests/test_api/test_ad_cabinet.py. Проверяем сериализацию, валидацию, дедуп
клиента, продвижение по воронке и агрегаты.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from database.models import AdClient, AdPayment, AdPublication, AdRequest
from web.api import ad_crm as api

# ----------------------------------------------------------------- helpers


def _db():
    """AsyncMock-сессия БД с синхронным ``add`` (как у настоящей AsyncSession)."""
    db = AsyncMock()
    db.add = MagicMock()
    return db


def _rows(items):
    """Результат execute с .all() (кортежные строки агрегатов)."""
    r = MagicMock()
    r.all.return_value = items
    return r


def _scalars(items):
    r = MagicMock()
    r.scalars.return_value.all.return_value = items
    return r


def _scalar_one_or_none(val):
    r = MagicMock()
    r.scalar_one_or_none.return_value = val
    return r


def _scalar_one(val):
    r = MagicMock()
    r.scalar_one.return_value = val
    return r


def _client(**kw):
    defaults = dict(id=1, author_vk_id=42, author_is_group=False, name="Иван", stage="detected")
    defaults.update(kw)
    return AdClient(**defaults)


def _request(**kw):
    defaults = dict(
        id=5,
        region_id=7,
        community_vk_id=-100,
        author_vk_id=42,
        author_is_group=False,
        author_name="Иван",
        peer_id=42,
        status="new",
    )
    defaults.update(kw)
    return AdRequest(**defaults)


# ----------------------------------------------------------------- list


async def test_list_clients_serializes_aggregates():
    client = _client()
    db = _db()
    db.execute = AsyncMock(return_value=_rows([(client, 1500, 2, 3)]))
    out = await api.list_clients(db=db)
    assert len(out["clients"]) == 1
    row = out["clients"][0]
    assert row["id"] == 1
    assert row["total_paid"] == 1500.0
    assert row["payments_count"] == 2
    assert row["publications_count"] == 3
    assert row["vk_url"] == "https://vk.com/id42"


async def test_list_clients_null_aggregates_default_zero():
    db = _db()
    db.execute = AsyncMock(return_value=_rows([(_client(), None, None, None)]))
    out = await api.list_clients(db=db)
    assert out["clients"][0]["total_paid"] == 0.0
    assert out["clients"][0]["payments_count"] == 0


# ----------------------------------------------------------------- create


async def test_create_client_ok():
    db = _db()
    db.execute = AsyncMock(return_value=_scalar_one_or_none(None))
    payload = api.ClientCreateIn(author_vk_id=42, name="Иван")
    out = await api.create_client(payload, db=db)
    assert out["author_vk_id"] == 42
    db.add.assert_called_once()
    db.commit.assert_awaited()


async def test_create_client_duplicate_409():
    db = _db()
    db.execute = AsyncMock(return_value=_scalar_one_or_none(_client()))
    with pytest.raises(HTTPException) as exc:
        await api.create_client(api.ClientCreateIn(author_vk_id=42), db=db)
    assert exc.value.status_code == 409


async def test_create_client_invalid_stage_400():
    db = _db()
    with pytest.raises(HTTPException) as exc:
        await api.create_client(api.ClientCreateIn(author_vk_id=42, stage="bogus"), db=db)
    assert exc.value.status_code == 400


# ----------------------------------------------------------------- detail


async def test_get_client_detail_totals():
    client = _client(id=3)
    pays = [AdPayment(id=1, client_id=3, amount=1000), AdPayment(id=2, client_id=3, amount=500)]
    pubs = [AdPublication(id=1, client_id=3, community_vk_id=-100, vk_post_id=9)]
    db = _db()
    db.get = AsyncMock(return_value=client)
    db.execute = AsyncMock(side_effect=[_scalars(pays), _scalars(pubs)])
    out = await api.get_client(3, db=db)
    assert out["total_paid"] == 1500.0
    assert out["publications_count"] == 1
    assert out["publications"][0]["vk_post_url"] == "https://vk.com/wall-100_9"


async def test_get_client_404():
    db = _db()
    db.get = AsyncMock(return_value=None)
    with pytest.raises(HTTPException) as exc:
        await api.get_client(999, db=db)
    assert exc.value.status_code == 404


# ----------------------------------------------------------------- update


async def test_update_client_applies_only_passed_fields():
    client = _client(name="Old", contact=None, stage="detected")
    db = _db()
    db.get = AsyncMock(return_value=client)
    out = await api.update_client(1, api.ClientUpdateIn(contact="+7999", stage="contacted"), db=db)
    assert out["contact"] == "+7999"
    assert out["stage"] == "contacted"
    assert out["name"] == "Old"  # не передавали — не трогаем


async def test_update_client_invalid_stage_400():
    db = _db()
    db.get = AsyncMock(return_value=_client())
    with pytest.raises(HTTPException) as exc:
        await api.update_client(1, api.ClientUpdateIn(stage="bogus"), db=db)
    assert exc.value.status_code == 400


# ----------------------------------------------------------------- upsert from request


async def test_upsert_from_request_creates_new():
    ar = _request()
    db = _db()
    db.get = AsyncMock(return_value=ar)
    db.execute = AsyncMock(return_value=_scalar_one_or_none(None))
    out = await api.upsert_from_request(5, db=db)
    assert out["created"] is True
    assert out["client"]["author_vk_id"] == 42
    db.flush.assert_awaited()


async def test_upsert_from_request_links_existing():
    ar = _request()
    existing = _client(id=11)
    db = _db()
    db.get = AsyncMock(return_value=ar)
    db.execute = AsyncMock(return_value=_scalar_one_or_none(existing))
    out = await api.upsert_from_request(5, db=db)
    assert out["created"] is False
    assert ar.client_id == 11


async def test_upsert_from_request_no_author_400():
    ar = _request(author_vk_id=None, peer_id=None)
    db = _db()
    db.get = AsyncMock(return_value=ar)
    with pytest.raises(HTTPException) as exc:
        await api.upsert_from_request(5, db=db)
    assert exc.value.status_code == 400


# ----------------------------------------------------------------- payments


async def test_create_payment_advances_stage_to_paid():
    client = _client(stage="contacted")
    db = _db()
    db.get = AsyncMock(return_value=client)
    out = await api.create_payment(api.PaymentCreateIn(client_id=1, amount=2000), db=db)
    assert out["amount"] == 2000.0
    assert client.stage == "paid"
    db.add.assert_called_once()


async def test_create_payment_lost_client_stays_lost():
    client = _client(stage="lost")
    db = _db()
    db.get = AsyncMock(return_value=client)
    await api.create_payment(api.PaymentCreateIn(client_id=1, amount=100), db=db)
    assert client.stage == "lost"


async def test_create_payment_non_positive_400():
    db = _db()
    db.get = AsyncMock(return_value=_client())
    with pytest.raises(HTTPException) as exc:
        await api.create_payment(api.PaymentCreateIn(client_id=1, amount=0), db=db)
    assert exc.value.status_code == 400


async def test_create_payment_client_404():
    db = _db()
    db.get = AsyncMock(return_value=None)
    with pytest.raises(HTTPException) as exc:
        await api.create_payment(api.PaymentCreateIn(client_id=99, amount=100), db=db)
    assert exc.value.status_code == 404


# ----------------------------------------------------------------- publications


async def test_create_publication_advances_stage():
    client = _client(stage="scheduled")
    db = _db()
    db.get = AsyncMock(return_value=client)
    out = await api.create_publication(
        api.PublicationCreateIn(community_vk_id=-100, client_id=1, vk_post_id=7, price=3000),
        db=db,
    )
    assert out["vk_post_url"] == "https://vk.com/wall-100_7"
    assert out["price"] == 3000.0
    assert client.stage == "published"


async def test_create_publication_does_not_downgrade_paid():
    client = _client(stage="paid")
    db = _db()
    db.get = AsyncMock(return_value=client)
    await api.create_publication(api.PublicationCreateIn(community_vk_id=-100, client_id=1), db=db)
    assert client.stage == "paid"


async def test_create_publication_invalid_status_400():
    db = _db()
    with pytest.raises(HTTPException) as exc:
        await api.create_publication(
            api.PublicationCreateIn(community_vk_id=-100, status="bogus"), db=db
        )
    assert exc.value.status_code == 400


async def test_create_publication_without_client_ok():
    db = _db()
    out = await api.create_publication(
        api.PublicationCreateIn(community_vk_id=-100, vk_post_id=3), db=db
    )
    assert out["community_vk_id"] == -100
    db.get.assert_not_called()  # client_id не передан — клиента не ищем


# ----------------------------------------------------------------- funnel


async def test_funnel_aggregates():
    db = _db()
    db.execute = AsyncMock(
        side_effect=[
            _rows([("detected", 4), ("paid", 2)]),
            _scalar_one(12000),
            _scalar_one(6),
        ]
    )
    out = await api.funnel(db=db)
    assert out["by_stage"]["detected"] == 4
    assert out["by_stage"]["paid"] == 2
    assert out["by_stage"]["contacted"] == 0  # незаполненная стадия → 0
    assert out["total_clients"] == 6
    assert out["total_paid"] == 12000.0
    assert out["publications_count"] == 6
