"""Tests for web/api/radar.py — подписки и лента (Ф0.2, мокнутая БД)."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from database.models_extended import RadarSource, RadarSubscription
from web.api import radar as radar_api


def _request(user=None):
    return SimpleNamespace(state=SimpleNamespace(user=user))


def _user(uid=1, role="radar"):
    return SimpleNamespace(id=uid, role=role)


class _FakeSession:
    """AsyncSessionLocal stand-in (паттерн test_auth_api.py)."""

    def __init__(self, scalar_results=(), all_results=()):
        self._scalars = list(scalar_results)  # очередь для scalar_one_or_none
        self._all = list(all_results)  # очередь для .all() / .scalars().all()
        self.added = []
        self.deleted = []
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, _stmt):
        result = MagicMock()
        result.scalar_one_or_none.return_value = self._scalars.pop(0) if self._scalars else None
        rows = self._all.pop(0) if self._all else []
        result.scalars.return_value.all.return_value = rows
        result.all.return_value = rows
        return result

    def add(self, obj):
        self.added.append(obj)

    async def delete(self, obj):
        self.deleted.append(obj)

    async def flush(self):
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = 100

    async def commit(self):
        self.committed = True

    async def refresh(self, obj):
        if getattr(obj, "id", None) is None:
            obj.id = 200


def test_unauthenticated_request_is_401():
    with pytest.raises(HTTPException) as exc:
        radar_api._current_user(_request(user=None))
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_create_subscription_rejects_bad_rss_url():
    body = radar_api.SubscriptionCreateIn(type="rss", value="ftp://nope")
    with pytest.raises(HTTPException) as exc:
        await radar_api.create_subscription(body, _request(_user()))
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_create_subscription_creates_source_and_sub():
    fake = _FakeSession(scalar_results=[None, None])  # source нет, подписки нет
    meta = {"key": "-218688001", "title": "Гоньба", "url": "https://vk.com/gonba"}
    with (
        patch.object(radar_api, "AsyncSessionLocal", lambda: fake),
        patch.object(radar_api, "_resolve_source_meta", return_value=meta),
    ):
        body = radar_api.SubscriptionCreateIn(type="vk", value="vk.com/gonba")
        result = await radar_api.create_subscription(body, _request(_user(uid=7)))

    assert result["created"] is True
    types_added = {type(o).__name__ for o in fake.added}
    assert types_added == {"RadarSource", "RadarSubscription"}
    sub = next(o for o in fake.added if isinstance(o, RadarSubscription))
    assert sub.user_id == 7
    assert fake.committed


@pytest.mark.asyncio
async def test_create_subscription_idempotent_for_existing():
    source = RadarSource(type="vk", key="-1", is_active=True)
    source.id = 5
    existing = RadarSubscription(user_id=7, source_id=5)
    existing.id = 33
    fake = _FakeSession(scalar_results=[source, existing])
    with (
        patch.object(radar_api, "AsyncSessionLocal", lambda: fake),
        patch.object(
            radar_api,
            "_resolve_source_meta",
            return_value={"key": "-1", "title": None, "url": None},
        ),
    ):
        body = radar_api.SubscriptionCreateIn(type="vk", value="-1")
        result = await radar_api.create_subscription(body, _request(_user(uid=7)))

    assert result == {
        "subscription_id": 33,
        "source": source.to_dict(),
        "created": False,
    }
    assert fake.added == []


@pytest.mark.asyncio
async def test_create_subscription_reactivates_inactive_source():
    source = RadarSource(type="vk", key="-1", is_active=False)
    source.id = 5
    fake = _FakeSession(scalar_results=[source, None])
    with (
        patch.object(radar_api, "AsyncSessionLocal", lambda: fake),
        patch.object(
            radar_api,
            "_resolve_source_meta",
            return_value={"key": "-1", "title": None, "url": None},
        ),
    ):
        body = radar_api.SubscriptionCreateIn(type="vk", value="-1")
        await radar_api.create_subscription(body, _request(_user()))
    assert source.is_active is True


@pytest.mark.asyncio
async def test_delete_subscription_only_own():
    fake = _FakeSession(scalar_results=[None])  # чужая/несуществующая → not found
    with patch.object(radar_api, "AsyncSessionLocal", lambda: fake):
        with pytest.raises(HTTPException) as exc:
            await radar_api.delete_subscription(99, _request(_user()))
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_delete_subscription_deletes():
    sub = RadarSubscription(user_id=1, source_id=2)
    sub.id = 10
    fake = _FakeSession(scalar_results=[sub])
    with patch.object(radar_api, "AsyncSessionLocal", lambda: fake):
        result = await radar_api.delete_subscription(10, _request(_user(uid=1)))
    assert result == {"deleted": True}
    assert fake.deleted == [sub]
    assert fake.committed


@pytest.mark.asyncio
async def test_feed_returns_items_with_source_and_cursor():
    from datetime import datetime

    from database.models_extended import RadarItem

    source = RadarSource(type="rss", key="https://e.com/f", title="Feed")
    source.id = 3
    item = RadarItem(
        source_id=3,
        external_id="x",
        url="https://e.com/1",
        published_at=datetime(2026, 6, 12, 9),
    )
    item.id = 42
    fake = _FakeSession(all_results=[[(item, source)]])
    with patch.object(radar_api, "AsyncSessionLocal", lambda: fake):
        result = await radar_api.get_feed(_request(_user()), before_id=None, limit=1)

    assert len(result["items"]) == 1
    assert result["items"][0]["source"]["title"] == "Feed"
    # Страница заполнена целиком → курсор указывает на последний id.
    assert result["next_before_id"] == 42


@pytest.mark.asyncio
async def test_feed_last_page_has_no_cursor():
    fake = _FakeSession(all_results=[[]])
    with patch.object(radar_api, "AsyncSessionLocal", lambda: fake):
        result = await radar_api.get_feed(_request(_user()), before_id=None, limit=30)
    assert result == {"items": [], "next_before_id": None}
