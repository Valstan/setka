"""Тесты планировщика отложенных постов рекламного кабинета (B1-b).

Сессия БД мокается (AsyncMock), VK-публикация/маршрутизация monkeypatch'атся —
без реальной БД и VK, в стиле tests/test_api/test_ad_cabinet.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

import modules.publisher.vk_publisher_extended as vpe
import modules.vk_token_router as token_router
from database.models import AdRequest, AdScheduledPost
from web.api import ad_cabinet as api

# Дата заведомо в будущем (МСК) — устойчиво к запуску тестов в любой год.
_FUTURE = "2090-01-01T12:00:00"
_FUTURE2 = "2090-01-02T09:30:00"


def _scalars_all(objs):
    r = MagicMock()
    r.scalars.return_value.all.return_value = objs
    return r


def _fake_publisher(publish_result=None, **overrides):
    pub = MagicMock()
    pub.publish_digest = AsyncMock(
        return_value=publish_result or {"success": True, "post_id": 999, "postponed": True}
    )
    pub.set_post_comments = AsyncMock(return_value={"success": True})
    pub.delete_post = AsyncMock(return_value={"success": True})
    for k, v in overrides.items():
        setattr(pub, k, v)
    return pub


def _patch_publish(monkeypatch, pub):
    monkeypatch.setattr(token_router, "load_vk_routing", AsyncMock(return_value=("utok", {})))
    monkeypatch.setattr(api, "_build_wall_attachment", lambda *a, **k: [])
    monkeypatch.setattr(vpe.VKPublisher, "create_with_policy", AsyncMock(return_value=pub))


def _create_db():
    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    return db


# ----------------------------------------------------------------- helpers


def test_msk_to_unix_converts_wall_clock():
    from datetime import datetime

    # 2070-01-01 03:00 МСК == 2070-01-01 00:00 UTC.
    dt = datetime(2070, 1, 1, 3, 0, 0)
    expected = int(datetime(2070, 1, 1, 0, 0, 0).replace(tzinfo=api.timezone.utc).timestamp())
    assert api._msk_to_unix(dt) == expected


def test_ad_scheduled_post_to_dict_derives_url():
    row = AdScheduledPost(
        community_vk_id=-100,
        publish_date=None,
        status="scheduled",
        vk_postponed_post_id=42,
    )
    d = row.to_dict()
    assert d["vk_post_url"] == "https://vk.com/wall-100_42"
    assert d["status"] == "scheduled"


# ----------------------------------------------------------------- create


async def test_create_scheduled_multi_date(monkeypatch):
    pub = _fake_publisher()
    _patch_publish(monkeypatch, pub)
    db = _create_db()

    out = await api.create_scheduled(
        api.ScheduleCreateIn(community_vk_id=-100, text="реклама", dates=[_FUTURE, _FUTURE2]),
        db=db,
    )

    assert out["scheduled"] == 2
    assert out["failed"] == 0
    assert len(out["created"]) == 2
    assert all(r["status"] == "scheduled" for r in out["created"])
    # На каждую дату — отдельный wall.post с publish_date.
    assert pub.publish_digest.await_count == 2
    for call in pub.publish_digest.await_args_list:
        assert call.kwargs["publish_date"] > 0
    db.commit.assert_awaited()


async def test_create_rejects_past_date(monkeypatch):
    pub = _fake_publisher()
    _patch_publish(monkeypatch, pub)
    db = _create_db()

    with pytest.raises(HTTPException) as exc:
        await api.create_scheduled(
            api.ScheduleCreateIn(community_vk_id=-100, text="x", dates=["2000-01-01T00:00:00"]),
            db=db,
        )
    assert exc.value.status_code == 400
    pub.publish_digest.assert_not_awaited()


async def test_create_rejects_empty_post(monkeypatch):
    pub = _fake_publisher()
    _patch_publish(monkeypatch, pub)
    db = _create_db()

    with pytest.raises(HTTPException) as exc:
        await api.create_scheduled(
            api.ScheduleCreateIn(community_vk_id=-100, text="  ", dates=[_FUTURE]),
            db=db,
        )
    assert exc.value.status_code == 400


async def test_create_requires_dates(monkeypatch):
    pub = _fake_publisher()
    _patch_publish(monkeypatch, pub)
    db = _create_db()

    with pytest.raises(HTTPException) as exc:
        await api.create_scheduled(
            api.ScheduleCreateIn(community_vk_id=-100, text="x", dates=[]), db=db
        )
    assert exc.value.status_code == 400


async def test_create_partial_failure(monkeypatch):
    """Одна дата падает на публикации → её строка failed, остальные scheduled."""
    pub = _fake_publisher()
    pub.publish_digest = AsyncMock(
        side_effect=[
            {"success": True, "post_id": 1},
            {"success": False, "error": "VK error 214 (too many postponed)"},
        ]
    )
    _patch_publish(monkeypatch, pub)
    db = _create_db()

    out = await api.create_scheduled(
        api.ScheduleCreateIn(community_vk_id=-100, text="x", dates=[_FUTURE, _FUTURE2]),
        db=db,
    )
    assert out["scheduled"] == 1
    assert out["failed"] == 1
    statuses = sorted(r["status"] for r in out["created"])
    assert statuses == ["failed", "scheduled"]
    failed = [r for r in out["created"] if r["status"] == "failed"][0]
    assert "postponed" in failed["error_message"]


async def test_create_closes_comments_when_disabled(monkeypatch):
    pub = _fake_publisher()
    _patch_publish(monkeypatch, pub)
    db = _create_db()

    await api.create_scheduled(
        api.ScheduleCreateIn(
            community_vk_id=-100, text="x", dates=[_FUTURE], comments_enabled=False
        ),
        db=db,
    )
    pub.set_post_comments.assert_awaited_once()
    _args, kwargs = pub.set_post_comments.await_args
    assert kwargs["enabled"] is False


async def test_create_keeps_comments_open_by_default(monkeypatch):
    pub = _fake_publisher()
    _patch_publish(monkeypatch, pub)
    db = _create_db()

    await api.create_scheduled(
        api.ScheduleCreateIn(community_vk_id=-100, text="x", dates=[_FUTURE]),
        db=db,
    )
    pub.set_post_comments.assert_not_awaited()


# ------------------------------------------------- B2: запланировать заявку


async def test_remove_original_deletes_suggested_and_publishes(monkeypatch):
    """remove_original + источник + успех → wall.delete оригинала, заявка published."""
    pub = _fake_publisher()
    _patch_publish(monkeypatch, pub)
    ar = AdRequest(community_vk_id=-170437443, vk_post_id=20278, status="new")
    db = _create_db()
    db.get = AsyncMock(return_value=ar)

    out = await api.create_scheduled(
        api.ScheduleCreateIn(
            community_vk_id=-170437443,
            text="реклама",
            dates=[_FUTURE],
            source_ad_request_id=7,
            remove_original=True,
        ),
        db=db,
    )

    assert out["scheduled"] == 1
    assert out["original_removed"] is True
    assert out["original_remove_error"] is None
    pub.delete_post.assert_awaited_once_with(-170437443, 20278)
    assert ar.status == "published"


async def test_remove_original_skipped_when_nothing_scheduled(monkeypatch):
    """Полный провал планирования → оригинал НЕ трогаем (не теряем заявку)."""
    pub = _fake_publisher()
    pub.publish_digest = AsyncMock(return_value={"success": False, "error": "VK 214"})
    _patch_publish(monkeypatch, pub)
    db = _create_db()
    db.get = AsyncMock()

    out = await api.create_scheduled(
        api.ScheduleCreateIn(
            community_vk_id=-100,
            text="x",
            dates=[_FUTURE],
            source_ad_request_id=7,
            remove_original=True,
        ),
        db=db,
    )

    assert out["scheduled"] == 0
    assert out["original_removed"] is False
    pub.delete_post.assert_not_awaited()
    db.get.assert_not_awaited()


async def test_remove_original_publishes_even_if_delete_fails(monkeypatch):
    """wall.delete оригинала упал → заявка всё равно published, ошибка в ответе."""
    pub = _fake_publisher()
    pub.delete_post = AsyncMock(return_value={"success": False, "error": "post not found"})
    _patch_publish(monkeypatch, pub)
    ar = AdRequest(community_vk_id=-100, vk_post_id=555, status="new")
    db = _create_db()
    db.get = AsyncMock(return_value=ar)

    out = await api.create_scheduled(
        api.ScheduleCreateIn(
            community_vk_id=-100,
            text="x",
            dates=[_FUTURE],
            source_ad_request_id=7,
            remove_original=True,
        ),
        db=db,
    )

    assert out["original_removed"] is False
    assert out["original_remove_error"] == "post not found"
    assert ar.status == "published"


async def test_no_source_request_no_removal(monkeypatch):
    """remove_original без source_ad_request_id — игнор (обычная раскладка)."""
    pub = _fake_publisher()
    _patch_publish(monkeypatch, pub)
    db = _create_db()
    db.get = AsyncMock()

    out = await api.create_scheduled(
        api.ScheduleCreateIn(community_vk_id=-100, text="x", dates=[_FUTURE], remove_original=True),
        db=db,
    )
    assert out["original_removed"] is False
    pub.delete_post.assert_not_awaited()
    db.get.assert_not_awaited()


# ----------------------------------------------------------------- list


async def test_list_scheduled_serializes():
    row = AdScheduledPost(
        community_vk_id=-100, publish_date=None, status="scheduled", vk_postponed_post_id=7
    )
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalars_all([row]))
    out = await api.list_scheduled(db=db)
    assert len(out["scheduled"]) == 1
    assert out["scheduled"][0]["vk_post_url"] == "https://vk.com/wall-100_7"


# ----------------------------------------------------------------- cancel


async def test_cancel_deletes_and_marks(monkeypatch):
    pub = _fake_publisher()
    _patch_publish(monkeypatch, pub)
    row = AdScheduledPost(
        community_vk_id=-100, publish_date=None, status="scheduled", vk_postponed_post_id=42
    )
    db = AsyncMock()
    db.get = AsyncMock(return_value=row)

    out = await api.cancel_scheduled(1, db=db)
    assert out["status"] == "cancelled"
    pub.delete_post.assert_awaited_once_with(-100, 42)


async def test_cancel_vk_fail_keeps_status(monkeypatch):
    pub = _fake_publisher()
    pub.delete_post = AsyncMock(return_value={"success": False, "error": "already published"})
    _patch_publish(monkeypatch, pub)
    row = AdScheduledPost(
        community_vk_id=-100, publish_date=None, status="scheduled", vk_postponed_post_id=42
    )
    db = AsyncMock()
    db.get = AsyncMock(return_value=row)

    out = await api.cancel_scheduled(1, db=db)
    assert out["cancel_error"] == "already published"
    assert row.status == "scheduled"  # статус НЕ изменён


async def test_cancel_404():
    db = AsyncMock()
    db.get = AsyncMock(return_value=None)
    with pytest.raises(HTTPException) as exc:
        await api.cancel_scheduled(999, db=db)
    assert exc.value.status_code == 404
