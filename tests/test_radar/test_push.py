"""Tests for modules/radar/push.py + push-API (Ф0.5)."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from database.models_extended import RadarPushSubscription
from modules.radar import push as push_mod
from web.api import radar as radar_api


@pytest.fixture
def _vapid_env(monkeypatch):
    # Реальный одноразовый ключ: roundtrip from_string + вывод публичного.
    from py_vapid import Vapid02, b64urlencode

    v = Vapid02()
    v.generate_keys()
    raw = v.private_key.private_numbers().private_value.to_bytes(32, "big")
    monkeypatch.setenv("RADAR_VAPID_PRIVATE_KEY", b64urlencode(raw))
    monkeypatch.setenv("RADAR_VAPID_SUBJECT", "mailto:test@example.com")


class TestVapid:
    def test_public_key_derived(self, _vapid_env):
        key = push_mod.vapid_public_key()
        assert key and not key.endswith("=")  # base64url без паддинга
        assert push_mod.push_configured()

    def test_not_configured(self, monkeypatch):
        monkeypatch.delenv("RADAR_VAPID_PRIVATE_KEY", raising=False)
        assert push_mod.vapid_public_key() is None
        assert push_mod.push_configured() is False


class _FakeSession:
    def __init__(self, rows, subs):
        self._rows = rows  # (user_id, source_id) подписки на источники
        self._subs = subs  # RadarPushSubscription-объекты
        self._calls = 0
        self.deleted = []
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, _stmt):
        self._calls += 1
        result = MagicMock()
        result.all.return_value = self._rows if self._calls == 1 else []
        result.scalars.return_value.all.return_value = self._subs
        return result

    async def delete(self, obj):
        self.deleted.append(obj)

    async def commit(self):
        self.committed = True


def _sub(uid=1, endpoint="https://push.example/abc"):
    s = RadarPushSubscription(user_id=uid, endpoint=endpoint, p256dh="p", auth="a")
    s.id = 1
    return s


@pytest.mark.asyncio
async def test_notify_skips_without_config(monkeypatch):
    monkeypatch.delenv("RADAR_VAPID_PRIVATE_KEY", raising=False)
    summary = await push_mod.notify_new_items({1: 5})
    assert summary == {"users": 0, "sent": 0, "dropped": 0}


@pytest.mark.asyncio
async def test_notify_sends_aggregated_count(_vapid_env):
    sub = _sub(uid=7)
    fake = _FakeSession(rows=[(7, 1), (7, 2)], subs=[sub])
    sent_payloads = []

    def fake_send(subscription, payload):
        sent_payloads.append(payload)
        return None

    with (
        patch("database.connection.AsyncSessionLocal", return_value=fake),
        patch.object(push_mod, "_send_webpush_sync", side_effect=fake_send),
    ):
        summary = await push_mod.notify_new_items({1: 3, 2: 4})

    assert summary == {"users": 1, "sent": 1, "dropped": 0}
    assert "7" in sent_payloads[0]  # 3+4 агрегированы в один push
    assert sub.last_success_at is not None
    assert fake.committed


@pytest.mark.asyncio
async def test_notify_drops_dead_subscription(_vapid_env):
    sub = _sub(uid=7)
    fake = _FakeSession(rows=[(7, 1)], subs=[sub])
    with (
        patch("database.connection.AsyncSessionLocal", return_value=fake),
        patch.object(push_mod, "_send_webpush_sync", return_value=410),
    ):
        summary = await push_mod.notify_new_items({1: 2})
    assert summary == {"users": 1, "sent": 0, "dropped": 1}
    assert fake.deleted == [sub]


@pytest.mark.asyncio
async def test_notify_never_raises(_vapid_env):
    with patch("database.connection.AsyncSessionLocal", side_effect=RuntimeError("db down")):
        summary = await push_mod.notify_new_items({1: 2})
    assert summary == {"users": 0, "sent": 0, "dropped": 0}


# ───────────── API ─────────────


def _request(user):
    return SimpleNamespace(state=SimpleNamespace(user=user))


def _user(uid=1):
    return SimpleNamespace(id=uid, role="radar")


class _ApiSession:
    def __init__(self, scalar_results=()):
        self._scalar = list(scalar_results)
        self.added = []
        self.deleted = []
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, _stmt):
        result = MagicMock()
        result.scalar_one_or_none.return_value = self._scalar.pop(0) if self._scalar else None
        return result

    def add(self, obj):
        self.added.append(obj)

    async def delete(self, obj):
        self.deleted.append(obj)

    async def commit(self):
        self.committed = True

    async def refresh(self, obj):
        if getattr(obj, "id", None) is None:
            obj.id = 11


@pytest.mark.asyncio
async def test_vapid_key_endpoint_404_when_unconfigured(monkeypatch):
    monkeypatch.delenv("RADAR_VAPID_PRIVATE_KEY", raising=False)
    with pytest.raises(HTTPException) as exc:
        await radar_api.get_vapid_public_key(_request(_user()))
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_create_push_subscription_new():
    fake = _ApiSession(scalar_results=[None])
    body = radar_api.PushSubscriptionIn(
        endpoint="https://push.example/endpoint1",
        keys=radar_api.PushKeysIn(p256dh="p", auth="a"),
    )
    with patch.object(radar_api, "AsyncSessionLocal", lambda: fake):
        result = await radar_api.create_push_subscription(body, _request(_user(uid=5)))
    assert result["created"] is True
    assert fake.added[0].user_id == 5


@pytest.mark.asyncio
async def test_create_push_subscription_rebinds_existing():
    existing = _sub(uid=1, endpoint="https://push.example/endpoint1")
    fake = _ApiSession(scalar_results=[existing])
    body = radar_api.PushSubscriptionIn(
        endpoint="https://push.example/endpoint1",
        keys=radar_api.PushKeysIn(p256dh="new-p", auth="new-a"),
    )
    with patch.object(radar_api, "AsyncSessionLocal", lambda: fake):
        result = await radar_api.create_push_subscription(body, _request(_user(uid=9)))
    assert result["created"] is False
    assert existing.user_id == 9
    assert existing.p256dh == "new-p"
    assert fake.added == []


@pytest.mark.asyncio
async def test_unsubscribe_only_own():
    fake = _ApiSession(scalar_results=[None])
    body = radar_api.PushUnsubscribeIn(endpoint="https://push.example/endpoint1")
    with patch.object(radar_api, "AsyncSessionLocal", lambda: fake):
        result = await radar_api.delete_push_subscription(body, _request(_user()))
    assert result == {"deleted": False}
    assert fake.deleted == []
