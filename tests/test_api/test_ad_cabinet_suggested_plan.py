"""Тесты API планировщика предложки (web/api/ad_cabinet, Этап 0).

Сессия БД мокается (AsyncMock), движок планирования (suggested_planner) и
VKPublisher monkeypatch'атся — в стиле tests/test_api/test_ad_scheduler:
проверяем контракт эндпойнтов, частичный успех и правила отмены по виду строки.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

import modules.publisher.vk_publisher_extended as vpe
from database.models import AdRequest, AdScheduledPost, Region
from modules.ad_cabinet import suggested_planner as sp
from modules.ad_cabinet.client_orders import OrderError
from web.api import ad_cabinet as api

_FUTURE = "2090-01-01T12:00:00"


def _scalars_all(objs):
    r = MagicMock()
    r.scalars.return_value.all.return_value = objs
    return r


def _scalar_one(obj):
    r = MagicMock()
    r.scalar_one_or_none.return_value = obj
    return r


def _region():
    return Region(
        id=1, code="mi", name="Малмыж", vk_group_id=158787639, is_active=True, neighbors="ur"
    )


def _request(**kw):
    defaults = dict(
        id=11,
        origin="suggested",
        community_vk_id=-158787639,
        vk_post_id=78276,
        author_vk_id=139799739,
        author_name="Анна",
        status="new",
        text_snapshot="текст",
    )
    defaults.update(kw)
    return AdRequest(**defaults)


def _row(**kw):
    defaults = dict(
        id=5,
        kind="post",
        community_vk_id=-158787639,
        text="t",
        publish_date=datetime(2090, 1, 1, 12, 0),
        status="scheduled",
        vk_postponed_post_id=999,
    )
    defaults.update(kw)
    return AdScheduledPost(**defaults)


def _db():
    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    db.refresh = AsyncMock()
    return db


# ---------------------------------------------------------------- options


async def test_options_returns_region_requests_dups_and_mode(monkeypatch):
    db = _db()
    db.execute = AsyncMock(side_effect=[_scalar_one(_region()), _scalars_all([_request()])])
    monkeypatch.setattr(
        sp,
        "all_dup_candidates",
        AsyncMock(return_value=[{"community_vk_id": -168170215, "name": "Уржум", "default": True}]),
    )
    # Живая предложка — отдельная функция; здесь она «ничего не нашла».
    monkeypatch.setattr(sp, "build_live_checker", AsyncMock(return_value=None))
    sync = AsyncMock(return_value={"fetched": 0, "inserted": 0, "revived": 0, "error": None})
    monkeypatch.setattr(sp, "sync_live_suggests", sync)
    monkeypatch.setenv("AD_SUGGESTED_VK_POSTPONE", "1")
    out = await api.suggested_plan_options(community_vk_id=158787639, db=db)
    assert out["region"]["community_vk_id"] == -158787639
    assert out["requests"][0]["id"] == 11
    assert out["dup_candidates"][0]["default"] is True
    assert out["floor_rub"] == sp.PLACEMENT_FLOOR_RUB
    assert out["mode"] == "vk_postpone"
    assert out["live"]["fetched"] == 0 and sync.await_count == 1
    db.commit.assert_not_awaited()  # ничего не завели — коммитить нечего


async def test_options_survives_token_loading_failure(monkeypatch):
    """Сбой получения токенов (БД/сеть) — форма показывает базу, а не 500."""
    db = _db()
    db.execute = AsyncMock(side_effect=[_scalar_one(_region()), _scalars_all([_request()])])
    monkeypatch.setattr(sp, "all_dup_candidates", AsyncMock(return_value=[]))
    monkeypatch.setattr(sp, "build_live_checker", AsyncMock(side_effect=RuntimeError("no db")))
    sync = AsyncMock()
    monkeypatch.setattr(sp, "sync_live_suggests", sync)
    out = await api.suggested_plan_options(community_vk_id=158787639, db=db)
    assert "no db" in out["live"]["error"] and out["requests"][0]["id"] == 11
    sync.assert_not_awaited()
    db.commit.assert_not_awaited()


async def test_options_commits_when_live_sync_inserted(monkeypatch):
    """Живая предложка завела заявку → commit до выборки, live в ответе."""
    db = _db()
    db.execute = AsyncMock(side_effect=[_scalar_one(_region()), _scalars_all([_request()])])
    monkeypatch.setattr(sp, "all_dup_candidates", AsyncMock(return_value=[]))
    monkeypatch.setattr(sp, "build_live_checker", AsyncMock(return_value=object()))
    monkeypatch.setattr(
        sp,
        "sync_live_suggests",
        AsyncMock(return_value={"fetched": 2, "inserted": 2, "revived": 0, "error": None}),
    )
    out = await api.suggested_plan_options(community_vk_id=158787639, db=db)
    assert out["live"]["inserted"] == 2
    db.commit.assert_awaited_once()


async def test_options_404_for_unknown_community():
    db = _db()
    db.execute = AsyncMock(return_value=_scalar_one(None))
    with pytest.raises(HTTPException) as ei:
        await api.suggested_plan_options(community_vk_id=1, db=db)
    assert ei.value.status_code == 404


# ---------------------------------------------------------------- create


def _patch_engine(monkeypatch, *, plan_result=None, plan_error=None):
    monkeypatch.setattr(vpe.VKPublisher, "create_with_policy", AsyncMock(return_value=MagicMock()))
    monkeypatch.setattr(
        sp, "resolve_dup_targets", AsyncMock(return_value=[(2, -168170215, "Уржум")])
    )
    calls = []

    async def plan_item(session, ar, **kw):
        calls.append({"ar": ar, **kw})
        if plan_error:
            raise plan_error
        return plan_result

    monkeypatch.setattr(sp, "plan_item", plan_item)
    return calls


async def test_create_rejects_empty_items():
    with pytest.raises(HTTPException) as ei:
        await api.suggested_plan_create(
            api.SuggestedPlanIn(community_vk_id=-158787639, items=[]), db=_db()
        )
    assert ei.value.status_code == 400


async def test_create_plans_item_and_serializes(monkeypatch):
    monkeypatch.setenv("AD_SUGGESTED_VK_POSTPONE", "1")
    original = _row(id=100, kind="suggested", price=550)
    repost = _row(id=101, kind="repost", source_post_id=100, community_vk_id=-168170215, price=550)
    calls = _patch_engine(
        monkeypatch,
        plan_result={
            "ok": True,
            "client_id": 10,
            "original": original,
            "reposts": [repost],
            "price_total": 1100.0,
            "order_ref": "abc",
        },
    )
    db = _db()
    ar = _request()
    db.execute = AsyncMock(side_effect=[_scalar_one(_region()), _scalar_one(ar)])
    payload = api.SuggestedPlanIn(
        community_vk_id=-158787639,
        items=[
            api.SuggestedPlanItemIn(
                request_id=11, publish_at=_FUTURE, price=1100, dup_community_ids=[-168170215]
            )
        ],
    )
    out = await api.suggested_plan_create(payload, db=db)
    assert out["mode"] == "vk_postpone"
    item = out["items"][0]
    assert item["ok"] and item["client_id"] == 10 and item["price_total"] == 1100.0
    assert item["original"]["kind"] == "suggested" and item["reposts"][0]["kind"] == "repost"
    assert calls[0]["publish_at"] == datetime(2090, 1, 1, 12, 0)
    assert calls[0]["price"] == 1100 and calls[0]["mode"] == "vk_postpone"
    db.commit.assert_awaited()


async def test_create_reports_order_error_per_item_and_continues(monkeypatch):
    monkeypatch.setenv("AD_SUGGESTED_VK_POSTPONE", "0")
    _patch_engine(monkeypatch, plan_error=OrderError("Цена ниже минимума"))
    db = _db()
    db.execute = AsyncMock(
        side_effect=[_scalar_one(_region()), _scalar_one(_request()), _scalar_one(None)]
    )
    payload = api.SuggestedPlanIn(
        community_vk_id=-158787639,
        items=[
            api.SuggestedPlanItemIn(request_id=11, publish_at=_FUTURE, price=1),
            api.SuggestedPlanItemIn(request_id=12, publish_at=_FUTURE),
        ],
    )
    out = await api.suggested_plan_create(payload, db=db)
    assert out["mode"] == "queue"
    assert out["items"][0]["ok"] is False and "минимума" in out["items"][0]["error"]
    assert out["items"][1]["ok"] is False and "не найдена" in out["items"][1]["error"]
    assert db.rollback.await_count == 2


async def test_create_rejects_request_from_other_community(monkeypatch):
    monkeypatch.setenv("AD_SUGGESTED_VK_POSTPONE", "0")
    _patch_engine(monkeypatch, plan_result={"ok": True})
    db = _db()
    db.execute = AsyncMock(
        side_effect=[_scalar_one(_region()), _scalar_one(_request(community_vk_id=-1))]
    )
    payload = api.SuggestedPlanIn(
        community_vk_id=-158787639,
        items=[api.SuggestedPlanItemIn(request_id=11, publish_at=_FUTURE)],
    )
    out = await api.suggested_plan_create(payload, db=db)
    assert out["items"][0]["ok"] is False and "другого сообщества" in out["items"][0]["error"]


# ---------------------------------------------------------------- list


async def test_list_groups_reposts_under_original():
    db = _db()
    original = _row(id=1, kind="suggested")
    reposts = [_row(id=2, kind="repost", source_post_id=1, community_vk_id=-200)]
    db.execute = AsyncMock(side_effect=[_scalars_all([original]), _scalars_all(reposts)])
    out = await api.suggested_plan_list(community_vk_id=158787639, db=db)
    assert len(out["plans"]) == 1
    assert out["plans"][0]["original"]["id"] == 1
    assert out["plans"][0]["reposts"][0]["community_vk_id"] == -200


# ---------------------------------------------------------------- cancel


async def test_cancel_repost_without_vk_post_does_not_touch_vk(monkeypatch):
    pub = MagicMock()
    pub.delete_post = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(vpe.VKPublisher, "create_with_policy", AsyncMock(return_value=pub))
    row = _row(id=7, kind="repost", source_post_id=1, vk_postponed_post_id=None)
    db = _db()
    db.execute = AsyncMock(return_value=_scalar_one(row))
    out = await api.cancel_scheduled(7, db=db)
    pub.delete_post.assert_not_awaited()
    assert out["status"] == "cancelled" and out["cancelled_reposts"] == 0


async def test_cancel_queue_mode_original_keeps_suggested_post_and_cascades(monkeypatch):
    pub = MagicMock()
    pub.delete_post = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(vpe.VKPublisher, "create_with_policy", AsyncMock(return_value=pub))
    row = _row(
        id=1, kind="suggested", vk_postponed_post_id=78276, next_attempt_at=datetime(2090, 1, 1)
    )
    child = _row(id=2, kind="repost", source_post_id=1, vk_postponed_post_id=None)
    db = _db()
    db.execute = AsyncMock(side_effect=[_scalar_one(row), _scalars_all([child])])
    out = await api.cancel_scheduled(1, db=db)
    pub.delete_post.assert_not_awaited()  # предложенный пост остаётся в предложке
    assert out["cancelled_reposts"] == 1 and child.status == "cancelled"


async def test_cancel_vk_postponed_original_deletes_from_vk(monkeypatch):
    pub = MagicMock()
    pub.delete_post = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(vpe.VKPublisher, "create_with_policy", AsyncMock(return_value=pub))
    row = _row(id=1, kind="suggested", vk_postponed_post_id=99001, next_attempt_at=None)
    db = _db()
    db.execute = AsyncMock(side_effect=[_scalar_one(row), _scalars_all([])])
    await api.cancel_scheduled(1, db=db)
    pub.delete_post.assert_awaited_once_with(-158787639, 99001)


async def test_cancel_published_row_is_refused(monkeypatch):
    pub = MagicMock()
    pub.delete_post = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(vpe.VKPublisher, "create_with_policy", AsyncMock(return_value=pub))
    row = _row(id=3, status="published")
    db = _db()
    db.execute = AsyncMock(return_value=_scalar_one(row))
    out = await api.cancel_scheduled(3, db=db)
    pub.delete_post.assert_not_awaited()
    assert out["status"] == "published" and "cancel_error" in out
