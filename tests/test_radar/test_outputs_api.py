"""Tests for web/api/radar.py — целевые выводы + пауза подписки (045, мокнутая БД)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from database.models_extended import RadarOutput, RadarSubscription
from web.api import radar as radar_api


def _request(user=None):
    return SimpleNamespace(state=SimpleNamespace(user=user))


def _user(uid=1):
    return SimpleNamespace(id=uid, role="radar")


class _FakeSession:
    def __init__(self, scalar_results=()):
        self._scalars = list(scalar_results)
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
        result.scalars.return_value.all.return_value = []
        return result

    def add(self, obj):
        self.added.append(obj)

    async def delete(self, obj):
        self.deleted.append(obj)

    async def commit(self):
        self.committed = True

    async def refresh(self, obj):
        if getattr(obj, "id", None) is None:
            obj.id = 200


@pytest.mark.asyncio
async def test_create_output_telegram_requires_target():
    body = radar_api.OutputCreateIn(type="telegram", target="")
    with pytest.raises(HTTPException) as exc:
        await radar_api.create_output(body, _request(_user()))
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_create_output_initializes_cursor_to_max_item_id():
    body = radar_api.OutputCreateIn(type="telegram", target="@me", bot_name="karman")
    fake = _FakeSession()
    with (
        patch.object(radar_api, "AsyncSessionLocal", return_value=fake),
        patch("modules.radar.delivery.max_item_id", new=AsyncMock(return_value=42)),
    ):
        result = await radar_api.create_output(body, _request(_user()))
    assert fake.added and isinstance(fake.added[0], RadarOutput)
    created = fake.added[0]
    assert created.last_item_id == 42  # новый вывод не выстрелит бэклогом
    assert created.config == {"bot_name": "KARMAN"}  # нормализован в upper
    assert result["type"] == "telegram"


@pytest.mark.asyncio
async def test_create_output_feed_allows_empty_target():
    body = radar_api.OutputCreateIn(type="feed")
    fake = _FakeSession()
    with (
        patch.object(radar_api, "AsyncSessionLocal", return_value=fake),
        patch("modules.radar.delivery.max_item_id", new=AsyncMock(return_value=0)),
    ):
        result = await radar_api.create_output(body, _request(_user()))
    assert result["type"] == "feed"
    assert fake.committed


@pytest.mark.asyncio
async def test_patch_output_not_found_is_404():
    body = radar_api.OutputPatchIn(is_active=False)
    fake = _FakeSession(scalar_results=[None])
    with patch.object(radar_api, "AsyncSessionLocal", return_value=fake):
        with pytest.raises(HTTPException) as exc:
            await radar_api.patch_output(7, body, _request(_user()))
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_patch_output_toggles_and_edits():
    output = RadarOutput(
        user_id=1, type="telegram", target="@a", mode="excerpt_link", is_active=True
    )
    output.id = 5
    output.config = {"bot_name": "KARMAN"}
    body = radar_api.OutputPatchIn(mode="full", is_active=False)
    fake = _FakeSession(scalar_results=[output])
    with patch.object(radar_api, "AsyncSessionLocal", return_value=fake):
        result = await radar_api.patch_output(5, body, _request(_user()))
    assert result["mode"] == "full"
    assert result["is_active"] is False


@pytest.mark.asyncio
async def test_delete_output_isolated_by_user():
    fake = _FakeSession(scalar_results=[None])  # чужой/несуществующий → не найден
    with patch.object(radar_api, "AsyncSessionLocal", return_value=fake):
        with pytest.raises(HTTPException) as exc:
            await radar_api.delete_output(5, _request(_user(uid=2)))
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_patch_subscription_pause():
    sub = RadarSubscription(user_id=1, source_id=3)
    sub.id = 9
    sub.is_active = True
    body = radar_api.SubscriptionPatchIn(is_active=False)
    fake = _FakeSession(scalar_results=[sub])
    with patch.object(radar_api, "AsyncSessionLocal", return_value=fake):
        result = await radar_api.patch_subscription(9, body, _request(_user()))
    assert result["is_active"] is False
    assert sub.is_active is False


@pytest.mark.asyncio
async def test_test_output_endpoint_passes_through():
    with patch(
        "modules.radar.delivery.send_test_output",
        new=AsyncMock(return_value={"ok": True, "detail": "Отправлено — проверьте канал"}),
    ):
        result = await radar_api.test_output(5, _request(_user()))
    assert result["ok"] is True
