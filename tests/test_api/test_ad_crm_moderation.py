"""Очередь модерации после аудита 2026-09-05 (PR 1.5): фото, район, заказ целиком.

Сессия БД мокается (AsyncMock) в стиле tests/test_api/test_ad_scheduler.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

import modules.vk_token_router as token_router
from database.models import AdClient, AdScheduledPost
from modules.ad_cabinet import client_orders
from web.api import ad_crm


def _row(**kw):
    defaults = dict(
        id=1,
        client_id=7,
        community_vk_id=-100,
        region_id=3,
        text="Полный текст " * 100,
        image_names=["a.jpg", "b.png"],
        publish_date=datetime(2090, 1, 1, 10, 0),
        status="pending",
        price=350,
        order_ref="ref-1",
    )
    defaults.update(kw)
    return AdScheduledPost(**defaults)


async def test_queue_returns_region_name_photos_and_full_text():
    row = _row()
    client = AdClient(id=7, name="Иван")
    db = AsyncMock()
    r = MagicMock()
    r.all.return_value = [(row, client, "Малмыж")]
    db.execute = AsyncMock(return_value=r)
    out = await ad_crm.moderation_queue(db=db)
    item = out["pending"][0]
    assert item["region_name"] == "Малмыж"
    assert item["photo_urls"] == [
        "/api/ad-crm/clients/7/photos/a.jpg",
        "/api/ad-crm/clients/7/photos/b.png",
    ]
    assert len(item["text"]) > 500  # текст не режется
    assert item["client"]["name"] == "Иван" and item["order_ref"] == "ref-1"


async def test_reject_requires_comment():
    db = AsyncMock()
    with pytest.raises(HTTPException) as ei:
        await ad_crm.moderation_reject(1, ad_crm.RejectIn(comment="  "), db=db)
    assert ei.value.status_code == 400


async def test_order_reject_marks_all_with_one_comment(monkeypatch):
    rows = [_row(id=1, region_id=1), _row(id=2, region_id=2)]
    db = AsyncMock()
    db.add = MagicMock()
    r = MagicMock()
    r.scalars.return_value.all.return_value = rows
    db.execute = AsyncMock(return_value=r)
    notified = []

    async def notify(session, client_id, text):
        notified.append((client_id, text))

    import modules.ad_cabinet.vk_bot.notify as n

    monkeypatch.setattr(n, "notify_client", notify)
    refunds = []

    async def refund(session, post):
        refunds.append(post.id)

    import modules.ad_cabinet.packages as pkgs

    monkeypatch.setattr(pkgs, "refund_post", refund)
    out = await ad_crm.moderation_order_reject("ref-1", ad_crm.RejectIn(comment="нет цены"), db=db)
    assert out == {"total": 2, "rejected": 2}
    assert all(p.status == "rejected" and p.moderation_comment == "нет цены" for p in rows)
    assert len(notified) == 1 and "нет цены" in notified[0][1]
    assert refunds == [1, 2]


async def test_order_approve_counts_ok_failed_and_past(monkeypatch):
    ok_row = _row(id=1, region_id=1)
    past_row = _row(id=2, region_id=2, publish_date=datetime(2000, 1, 1, 10, 0))
    db = AsyncMock()
    db.add = MagicMock()
    r = MagicMock()
    r.scalars.return_value.all.return_value = [ok_row, past_row]
    db.execute = AsyncMock(return_value=r)
    db.get = AsyncMock(return_value=AdClient(id=7, name="Иван", approved_posts_count=0))
    monkeypatch.setattr(token_router, "load_vk_routing", AsyncMock(return_value=("utok", {})))

    async def fake_approve(session, post, **kw):
        if post.publish_date.year < 2001 and kw.get("new_publish_at") is None:
            raise client_orders.OrderError("дата прошла")
        post.status = "scheduled"
        return post

    monkeypatch.setattr(client_orders, "approve_post", fake_approve)
    notified = []

    async def notify(session, client_id, text):
        notified.append(text)

    import modules.ad_cabinet.vk_bot.notify as n

    monkeypatch.setattr(n, "notify_client", notify)
    out = await ad_crm.moderation_order_approve("ref-1", db=db)
    assert out["approved"] == 1 and out["past_date"] == 1 and out["failed"] == 0
    assert past_row.status == "pending" and ok_row.status == "scheduled"
    assert len(notified) == 1


async def test_count_endpoint_shape():
    db = AsyncMock()
    r = MagicMock()
    r.scalar_one.return_value = 3
    db.execute = AsyncMock(return_value=r)
    out = await ad_crm.moderation_count(db=db)
    assert out == {"pending": 3, "unread": 3}
