"""Тесты CRM рекламного кабинета (web/api/ad_crm) — блок C.

Сессия БД мокается (AsyncMock): без реальной БД, в стиле
tests/test_api/test_ad_cabinet.py. Проверяем сериализацию, валидацию, дедуп
клиента, продвижение по воронке и агрегаты.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from database.models import (
    AdClient,
    AdInteraction,
    AdOrderItem,
    AdPayment,
    AdPublication,
    AdRequest,
)
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


def _scalars_first(item):
    r = MagicMock()
    r.scalars.return_value.first.return_value = item
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
    # row: (client, total_paid, total_awaiting, payments_count, publications_count)
    db.execute = AsyncMock(return_value=_rows([(client, 1500, 300, 2, 3)]))
    out = await api.list_clients(db=db)
    assert len(out["clients"]) == 1
    row = out["clients"][0]
    assert row["id"] == 1
    assert row["total_paid"] == 1500.0
    assert row["total_awaiting"] == 300.0
    assert row["payments_count"] == 2
    assert row["publications_count"] == 3
    assert row["vk_url"] == "https://vk.com/id42"


async def test_list_clients_null_aggregates_default_zero():
    db = _db()
    db.execute = AsyncMock(return_value=_rows([(_client(), None, None, None, None)]))
    out = await api.list_clients(db=db)
    assert out["clients"][0]["total_paid"] == 0.0
    assert out["clients"][0]["total_awaiting"] == 0.0
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
    # payment + interaction-событие → два add
    assert db.add.call_count == 2


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
            _scalar_one(12000),  # total_paid
            _scalar_one(1500),  # total_awaiting
            _scalar_one(6),  # publications_count
        ]
    )
    out = await api.funnel(db=db)
    assert out["by_stage"]["detected"] == 4
    assert out["by_stage"]["paid"] == 2
    assert out["by_stage"]["contacted"] == 0  # незаполненная стадия → 0
    assert out["total_clients"] == 6
    assert out["total_paid"] == 12000.0
    assert out["total_awaiting"] == 1500.0
    assert out["publications_count"] == 6


# ----------------------------------------------------------------- wiring (UI PR)


def test_routes_registered():
    """Страница /ad-crm и роутер /api/ad-crm подключены в приложении (блок C UI)."""
    import main

    paths = {getattr(r, "path", None) for r in main.app.routes}
    assert "/ad-crm" in paths
    assert "/api/ad-crm/funnel" in paths
    assert "/api/ad-crm/clients" in paths
    assert "/api/ad-crm/clients/{client_id}/timeline" in paths
    assert "/api/ad-crm/interactions" in paths
    assert "/api/ad-crm/banks" in paths
    assert "/api/ad-crm/payments/{payment_id}" in paths
    assert "/api/ad-crm/order-items" in paths
    assert "/api/ad-crm/clients/{client_id}/order-items" in paths
    assert "/api/ad-crm/order-items/from-request/{request_id}" in paths
    assert "/api/ad-crm/clients/{client_id}/thread" in paths
    assert "/api/ad-crm/clients/{client_id}/reply" in paths
    assert "/api/ad-crm/stats/timeseries" in paths


# ----------------------------------------------------------------- stats / charts (PR-7)


async def test_timeseries_fills_gaps_and_maps_counts():
    from datetime import datetime, timedelta

    # Anchor to the rolling window [today-(days-1) … today] the endpoint builds,
    # so the test doesn't rot as real time moves past hardcoded dates.
    today = datetime.utcnow().date()
    offer_day = today - timedelta(days=4)  # oldest day still inside a 5-day window
    paid_day = today - timedelta(days=2)

    db = _db()
    # offers: одна дата с 3 заявками; paid: одна дата с 2 оплатами на 4000
    db.execute = AsyncMock(
        side_effect=[
            _rows([(offer_day, 3)]),
            _rows([(paid_day, 2, 4000)]),
        ]
    )
    out = await api.stats_timeseries(days=5, db=db)
    assert out["days"] == 5
    assert len(out["labels"]) == 5
    assert len(out["offers"]) == 5
    assert len(out["paid_count"]) == 5
    # ряды непрерывны: сумма по offers = 3, по paid_count = 2
    assert sum(out["offers"]) == 3
    assert sum(out["paid_count"]) == 2
    assert sum(out["paid_amount"]) == 4000.0


async def test_timeseries_empty_returns_zero_filled():
    db = _db()
    db.execute = AsyncMock(side_effect=[_rows([]), _rows([])])
    out = await api.stats_timeseries(days=7, db=db)
    assert len(out["labels"]) == 7
    assert sum(out["offers"]) == 0
    assert sum(out["paid_count"]) == 0


async def test_timeseries_clamps_days():
    db = _db()
    db.execute = AsyncMock(side_effect=[_rows([]), _rows([])])
    out = await api.stats_timeseries(days=9999, db=db)
    assert out["days"] == 365


# ----------------------------------------------------------------- client chat (PR-5)


class _FakeDialogs:
    """Заглушка VKDialogsChecker: fetch_history возвращает заданный список."""

    _history = []

    def __init__(self, *a, **k):
        pass

    def fetch_history(self, group_id, peer_id, count):
        return list(self._history)


async def test_client_thread_no_dialog():
    client = _client(id=3)
    db = _db()
    db.get = AsyncMock(return_value=client)
    db.execute = AsyncMock(return_value=_scalars_first(None))  # нет заявки с peer
    out = await api.client_thread(3, db=db)
    assert out["reason"] == "no_dialog"


async def test_client_thread_404():
    db = _db()
    db.get = AsyncMock(return_value=None)
    with pytest.raises(HTTPException) as exc:
        await api.client_thread(9, db=db)
    assert exc.value.status_code == 404


async def test_client_thread_returns_two_way(monkeypatch):
    import modules.notifications.vk_dialogs_checker as vdc
    import modules.vk_token_router as tr

    _FakeDialogs._history = [
        {"out": False, "from_name": "Иван", "text": "Почём реклама?", "date": 1, "attachments": 0},
        {"out": True, "from_name": None, "text": "1000 ₽", "date": 2, "attachments": 0},
    ]
    monkeypatch.setattr(vdc, "VKDialogsChecker", _FakeDialogs)
    monkeypatch.setattr(tr, "load_vk_routing", AsyncMock(return_value=("utok", {})))

    client = _client(id=3)
    ar = _request(client_id=3, peer_id=42, community_vk_id=-100, origin="inbound_dm")
    db = _db()
    db.get = AsyncMock(return_value=client)
    db.execute = AsyncMock(return_value=_scalars_first(ar))
    out = await api.client_thread(3, db=db)
    assert len(out["messages"]) == 2
    assert out["messages"][1]["out"] is True
    assert out["peer_id"] == 42
    assert out["dialog_url"] == "https://vk.com/gim100?sel=42"


async def test_client_reply_success_logs(monkeypatch):
    import modules.notifications.vk_actions as va
    import modules.vk_token_router as tr

    monkeypatch.setattr(
        va, "send_message", MagicMock(return_value={"success": True, "via": "community-token"})
    )
    monkeypatch.setattr(tr, "load_vk_routing", AsyncMock(return_value=("utok", {})))

    client = _client(id=3)
    ar = _request(client_id=3, peer_id=42, community_vk_id=-100)
    db = _db()
    db.get = AsyncMock(return_value=client)
    db.execute = AsyncMock(return_value=_scalars_first(ar))
    out = await api.client_reply(3, api.ClientReplyIn(message="Здравствуйте!"), db=db)
    assert out["success"] is True
    logs = _added_interactions(db)
    assert logs and logs[0].kind == "reply_sent"


async def test_client_reply_blocked_returns_deeplink(monkeypatch):
    import modules.notifications.vk_actions as va
    import modules.vk_token_router as tr

    monkeypatch.setattr(
        va,
        "send_message",
        MagicMock(
            return_value={
                "allowed": False,
                "personal_deeplink": "https://vk.com/im?sel=42",
                "error_code": 901,
            }
        ),
    )
    monkeypatch.setattr(tr, "load_vk_routing", AsyncMock(return_value=("utok", {})))

    client = _client(id=3)
    ar = _request(client_id=3, peer_id=42, community_vk_id=-100)
    db = _db()
    db.get = AsyncMock(return_value=client)
    db.execute = AsyncMock(return_value=_scalars_first(ar))
    out = await api.client_reply(3, api.ClientReplyIn(message="привет"), db=db)
    assert out["allowed"] is False
    assert "vk.com/im" in out["personal_deeplink"]


async def test_client_reply_empty_message_400():
    db = _db()
    db.get = AsyncMock(return_value=_client(id=3))
    with pytest.raises(HTTPException) as exc:
        await api.client_reply(3, api.ClientReplyIn(message="  "), db=db)
    assert exc.value.status_code == 400


async def test_client_reply_no_dialog_400():
    db = _db()
    db.get = AsyncMock(return_value=_client(id=3))
    db.execute = AsyncMock(return_value=_scalars_first(None))
    with pytest.raises(HTTPException) as exc:
        await api.client_reply(3, api.ClientReplyIn(message="привет"), db=db)
    assert exc.value.status_code == 400


# ----------------------------------------------------------------- order items (PR-4)


async def test_list_order_items_totals_quantity():
    client = _client(id=3)
    items = [
        AdOrderItem(id=1, client_id=3, description="баннер", quantity=2, status="planned"),
        AdOrderItem(id=2, client_id=3, description="репост", quantity=3, status="published"),
    ]
    db = _db()
    db.get = AsyncMock(return_value=client)
    db.execute = AsyncMock(return_value=_scalars(items))
    out = await api.list_order_items(3, db=db)
    assert out["total_quantity"] == 5
    assert len(out["order_items"]) == 2


async def test_list_order_items_client_404():
    db = _db()
    db.get = AsyncMock(return_value=None)
    with pytest.raises(HTTPException) as exc:
        await api.list_order_items(9, db=db)
    assert exc.value.status_code == 404


async def test_create_order_item_manual_logs():
    client = _client(id=3)
    db = _db()
    db.get = AsyncMock(return_value=client)
    out = await api.create_order_item(
        api.OrderItemCreateIn(client_id=3, description="баннер на месяц", quantity=4), db=db
    )
    assert out["quantity"] == 4
    assert out["description"] == "баннер на месяц"
    logs = _added_interactions(db)
    assert logs and logs[0].kind == "order_item"


async def test_create_order_item_invalid_status_400():
    db = _db()
    db.get = AsyncMock(return_value=_client())
    with pytest.raises(HTTPException) as exc:
        await api.create_order_item(
            api.OrderItemCreateIn(client_id=1, description="x", status="bogus"), db=db
        )
    assert exc.value.status_code == 400


async def test_create_order_item_bad_quantity_400():
    db = _db()
    db.get = AsyncMock(return_value=_client())
    with pytest.raises(HTTPException) as exc:
        await api.create_order_item(
            api.OrderItemCreateIn(client_id=1, description="x", quantity=0), db=db
        )
    assert exc.value.status_code == 400


async def test_create_order_item_parses_period():
    client = _client(id=3)
    db = _db()
    db.get = AsyncMock(return_value=client)
    out = await api.create_order_item(
        api.OrderItemCreateIn(
            client_id=3, description="x", period_start="2026-06-01", period_end="2026-06-30"
        ),
        db=db,
    )
    assert out["period_start"] == "2026-06-01"
    assert out["period_end"] == "2026-06-30"


async def test_order_item_from_request_uses_text_snapshot():
    ar = _request(client_id=3, text_snapshot="Продаю гараж недорого")
    db = _db()
    db.get = AsyncMock(return_value=ar)
    out = await api.create_order_item_from_request(5, db=db)
    assert out["ad_request_id"] == 5
    assert "гараж" in out["description"]
    logs = _added_interactions(db)
    assert logs and logs[0].kind == "order_item"


async def test_order_item_from_request_unlinked_400():
    ar = _request(client_id=None)
    db = _db()
    db.get = AsyncMock(return_value=ar)
    with pytest.raises(HTTPException) as exc:
        await api.create_order_item_from_request(5, db=db)
    assert exc.value.status_code == 400


async def test_update_order_item_changes_status_and_qty():
    item = AdOrderItem(id=1, client_id=3, description="x", quantity=1, status="planned")
    db = _db()
    db.get = AsyncMock(return_value=item)
    out = await api.update_order_item(
        1, api.OrderItemUpdateIn(status="published", quantity=2), db=db
    )
    assert out["status"] == "published"
    assert out["quantity"] == 2


async def test_update_order_item_invalid_status_400():
    item = AdOrderItem(id=1, client_id=3, description="x", quantity=1, status="planned")
    db = _db()
    db.get = AsyncMock(return_value=item)
    with pytest.raises(HTTPException) as exc:
        await api.update_order_item(1, api.OrderItemUpdateIn(status="bogus"), db=db)
    assert exc.value.status_code == 400


async def test_delete_order_item_ok():
    item = AdOrderItem(id=1, client_id=3, description="x", quantity=1)
    db = _db()
    db.get = AsyncMock(return_value=item)
    out = await api.delete_order_item(1, db=db)
    assert out["success"] is True
    db.delete.assert_awaited()


# ----------------------------------------------------------------- interactions / timeline


def _added_interactions(db):
    """Извлечь все AdInteraction, переданные в db.add (для проверки логирования)."""
    return [
        c.args[0] for c in db.add.call_args_list if c.args and isinstance(c.args[0], AdInteraction)
    ]


async def test_create_payment_logs_interaction():
    client = _client(stage="contacted")
    db = _db()
    db.get = AsyncMock(return_value=client)
    await api.create_payment(api.PaymentCreateIn(client_id=1, amount=2000, method="карта"), db=db)
    logs = _added_interactions(db)
    assert len(logs) == 1
    assert logs[0].kind == "payment_added"
    assert logs[0].client_id == 1
    assert "2000" in logs[0].summary
    db.flush.assert_awaited()


async def test_delete_payment_logs_interaction():
    pay = AdPayment(id=9, client_id=3, amount=500)
    db = _db()
    db.get = AsyncMock(return_value=pay)
    out = await api.delete_payment(9, db=db)
    assert out["success"] is True
    logs = _added_interactions(db)
    assert logs and logs[0].kind == "payment_deleted"
    assert logs[0].client_id == 3
    db.delete.assert_awaited()


async def test_create_publication_logs_interaction():
    client = _client(stage="scheduled")
    db = _db()
    db.get = AsyncMock(return_value=client)
    await api.create_publication(
        api.PublicationCreateIn(community_vk_id=-100, client_id=1, vk_post_id=7), db=db
    )
    logs = _added_interactions(db)
    assert logs and logs[0].kind == "published"
    assert logs[0].client_id == 1


async def test_update_client_logs_stage_change_only():
    client = _client(stage="detected")
    db = _db()
    db.get = AsyncMock(return_value=client)
    await api.update_client(1, api.ClientUpdateIn(stage="contacted"), db=db)
    logs = _added_interactions(db)
    assert len(logs) == 1 and logs[0].kind == "status_changed"
    assert "detected" in logs[0].summary and "contacted" in logs[0].summary


async def test_update_client_no_log_when_stage_unchanged():
    client = _client(stage="detected")
    db = _db()
    db.get = AsyncMock(return_value=client)
    await api.update_client(1, api.ClientUpdateIn(contact="+7999"), db=db)
    assert _added_interactions(db) == []


async def test_upsert_from_request_backfills_and_logs():
    ar = _request()
    db = _db()
    db.get = AsyncMock(return_value=ar)
    db.execute = AsyncMock(return_value=_scalar_one_or_none(None))
    await api.upsert_from_request(5, db=db)
    # backfill update + (lookup) — db.execute вызывался; событие залогировано
    logs = _added_interactions(db)
    assert logs and logs[0].kind == "detected"
    assert logs[0].ad_request_id == 5
    assert db.execute.await_count >= 2  # lookup + backfill update


async def test_client_timeline_returns_events_desc():
    client = _client(id=3)
    events = [
        AdInteraction(id=2, client_id=3, kind="payment_added", summary="Оплата 1000 ₽"),
        AdInteraction(id=1, client_id=3, kind="detected", summary="Заведён клиент"),
    ]
    db = _db()
    db.get = AsyncMock(return_value=client)
    db.execute = AsyncMock(return_value=_scalars(events))
    out = await api.client_timeline(3, db=db)
    assert [e["id"] for e in out["timeline"]] == [2, 1]
    assert out["timeline"][0]["kind"] == "payment_added"


async def test_client_timeline_404():
    db = _db()
    db.get = AsyncMock(return_value=None)
    with pytest.raises(HTTPException) as exc:
        await api.client_timeline(999, db=db)
    assert exc.value.status_code == 404


async def test_create_interaction_manual_note():
    client = _client(id=3)
    db = _db()
    db.get = AsyncMock(return_value=client)
    out = await api.create_interaction(
        api.InteractionCreateIn(client_id=3, summary="Договорились на вторник"), db=db
    )
    assert out["kind"] == "note"
    assert out["summary"] == "Договорились на вторник"
    db.add.assert_called_once()


async def test_create_interaction_empty_summary_400():
    db = _db()
    db.get = AsyncMock(return_value=_client())
    with pytest.raises(HTTPException) as exc:
        await api.create_interaction(api.InteractionCreateIn(client_id=1, summary="  "), db=db)
    assert exc.value.status_code == 400


async def test_create_interaction_client_404():
    db = _db()
    db.get = AsyncMock(return_value=None)
    with pytest.raises(HTTPException) as exc:
        await api.create_interaction(api.InteractionCreateIn(client_id=9, summary="x"), db=db)
    assert exc.value.status_code == 404


async def test_update_interaction_edits_summary():
    rec = AdInteraction(id=1, client_id=3, kind="note", summary="old")
    db = _db()
    db.get = AsyncMock(return_value=rec)
    out = await api.update_interaction(1, api.InteractionUpdateIn(summary="new"), db=db)
    assert out["summary"] == "new"


async def test_update_interaction_404():
    db = _db()
    db.get = AsyncMock(return_value=None)
    with pytest.raises(HTTPException) as exc:
        await api.update_interaction(1, api.InteractionUpdateIn(summary="x"), db=db)
    assert exc.value.status_code == 404


async def test_delete_interaction_ok():
    rec = AdInteraction(id=1, client_id=3, kind="note", summary="x")
    db = _db()
    db.get = AsyncMock(return_value=rec)
    out = await api.delete_interaction(1, db=db)
    assert out["success"] is True
    db.delete.assert_awaited()


# ----------------------------------------------------------------- payments: bank / status (PR-3)


async def test_create_payment_with_bank_and_status_paid():
    client = _client(stage="contacted")
    db = _db()
    db.get = AsyncMock(return_value=client)
    out = await api.create_payment(
        api.PaymentCreateIn(client_id=1, amount=2000, bank="Сбербанк", status="paid"), db=db
    )
    assert out["bank"] == "Сбербанк"
    assert out["status"] == "paid"
    assert out["paid_confirmed_at"] is not None
    assert client.stage == "paid"
    logs = _added_interactions(db)
    assert logs[0].kind == "payment_added"


async def test_create_payment_awaiting_does_not_advance_stage():
    client = _client(stage="contacted")
    db = _db()
    db.get = AsyncMock(return_value=client)
    out = await api.create_payment(
        api.PaymentCreateIn(client_id=1, amount=2000, status="awaiting", bank="Т-Банк"), db=db
    )
    assert out["status"] == "awaiting"
    assert out["paid_confirmed_at"] is None
    assert client.stage == "contacted"  # ожидание оплаты не двигает воронку
    logs = _added_interactions(db)
    assert logs[0].kind == "payment_awaiting"


async def test_create_payment_invalid_status_400():
    db = _db()
    db.get = AsyncMock(return_value=_client())
    with pytest.raises(HTTPException) as exc:
        await api.create_payment(
            api.PaymentCreateIn(client_id=1, amount=100, status="bogus"), db=db
        )
    assert exc.value.status_code == 400


async def test_update_payment_awaiting_to_paid_advances_and_logs():
    pay = AdPayment(id=5, client_id=3, amount=2000, status="awaiting")
    client = _client(id=3, stage="published")
    db = _db()
    # первый get — оплата, второй — клиент (для продвижения стадии)
    db.get = AsyncMock(side_effect=[pay, client])
    out = await api.update_payment(5, api.PaymentUpdateIn(status="paid", bank="ВТБ"), db=db)
    assert out["status"] == "paid"
    assert out["bank"] == "ВТБ"
    assert out["paid_confirmed_at"] is not None
    assert client.stage == "paid"
    logs = _added_interactions(db)
    assert logs and logs[0].kind == "payment_paid"


async def test_update_payment_edit_amount_no_status_change():
    pay = AdPayment(id=5, client_id=3, amount=1000, status="paid")
    db = _db()
    db.get = AsyncMock(return_value=pay)
    out = await api.update_payment(5, api.PaymentUpdateIn(amount=1500), db=db)
    assert out["amount"] == 1500.0
    # стадия не трогается (не переход в paid) → события payment_paid нет
    assert _added_interactions(db) == []


async def test_update_payment_404():
    db = _db()
    db.get = AsyncMock(return_value=None)
    with pytest.raises(HTTPException) as exc:
        await api.update_payment(9, api.PaymentUpdateIn(amount=100), db=db)
    assert exc.value.status_code == 404


async def test_list_banks_returns_fixed_list_and_stats():
    db = _db()
    db.execute = AsyncMock(return_value=_rows([("Сбербанк", 5, 10000), ("Т-Банк", 2, 4000)]))
    out = await api.list_banks(db=db)
    assert "Сбербанк" in out["banks"]
    assert out["stats"][0]["bank"] == "Сбербанк"
    assert out["stats"][0]["count"] == 5
    assert out["stats"][0]["total"] == 10000.0
