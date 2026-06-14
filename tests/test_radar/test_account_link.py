"""Tests for modules/radar/account_link — привязка Telegram-лички (Радиоточка)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from database.models_extended import RadarOutput
from modules.radar import account_link


class _FakeRedis:
    def __init__(self):
        self.store = {}

    def setex(self, k, ttl, v):
        self.store[k] = v

    def get(self, k):
        v = self.store.get(k)
        return v.encode() if isinstance(v, str) else v

    def delete(self, k):
        self.store.pop(k, None)


def test_generate_and_resolve_code_roundtrip():
    fake = _FakeRedis()
    with patch.object(account_link, "_redis", return_value=fake):
        code = account_link.generate_link_code(7, channel="telegram")
        assert code and len(code) == account_link._CODE_LEN
        resolved = account_link.resolve_link_code(code)
        assert resolved == ("telegram", 7)
        # одноразовый — после потребления исчез
        assert account_link.resolve_link_code(code) is None


def test_resolve_unknown_code_is_none():
    fake = _FakeRedis()
    with patch.object(account_link, "_redis", return_value=fake):
        assert account_link.resolve_link_code("NOPE12") is None


def test_generate_returns_none_without_redis():
    with patch.object(account_link, "_redis", return_value=None):
        assert account_link.generate_link_code(1) is None


class _LinkSession:
    def __init__(self, existing=None):
        self._existing = existing
        self.added = []
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, _stmt):
        result = MagicMock()
        result.scalar_one_or_none.return_value = self._existing
        return result

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed = True

    async def refresh(self, obj):
        if getattr(obj, "id", None) is None:
            obj.id = 55


@pytest.mark.asyncio
async def test_link_telegram_creates_output():
    fake = _LinkSession(existing=None)
    with (
        patch.object(account_link, "resolve_link_code", return_value=("telegram", 7)),
        patch("modules.radar.delivery.max_item_id", new=AsyncMock(return_value=99)),
    ):
        res = await account_link.link_telegram(
            "ABC123", 12345, display_name="Иван", bot_name="karman", session_factory=lambda: fake
        )
    assert res["status"] == "linked"
    out = fake.added[0]
    assert isinstance(out, RadarOutput)
    assert out.type == "telegram"
    assert out.target == "12345"
    assert out.user_id == 7
    assert out.last_item_id == 99  # без бэклога
    assert out.config == {"bot_name": "KARMAN"}
    assert "Иван" in out.title


@pytest.mark.asyncio
async def test_link_telegram_invalid_code():
    with patch.object(account_link, "resolve_link_code", return_value=None):
        res = await account_link.link_telegram("BAD", 1, session_factory=lambda: _LinkSession())
    assert res["status"] == "invalid"


@pytest.mark.asyncio
async def test_link_telegram_existing_is_idempotent():
    existing = RadarOutput(user_id=7, type="telegram", target="12345", is_active=True)
    existing.id = 5
    fake = _LinkSession(existing=existing)
    with patch.object(account_link, "resolve_link_code", return_value=("telegram", 7)):
        res = await account_link.link_telegram("ABC", 12345, session_factory=lambda: fake)
    assert res["status"] == "exists"
    assert not fake.added  # дубля не создаём


@pytest.mark.asyncio
async def test_link_telegram_reactivates_disabled_existing():
    existing = RadarOutput(user_id=7, type="telegram", target="12345", is_active=False)
    existing.id = 5
    fake = _LinkSession(existing=existing)
    with patch.object(account_link, "resolve_link_code", return_value=("telegram", 7)):
        res = await account_link.link_telegram("ABC", 12345, session_factory=lambda: fake)
    assert res["status"] == "exists"
    assert existing.is_active is True
    assert fake.committed


@pytest.mark.asyncio
async def test_link_vk_creates_vk_dm_output():
    fake = _LinkSession(existing=None)
    with (
        patch.object(account_link, "resolve_link_code", return_value=("vk", 7)),
        patch("modules.radar.delivery.max_item_id", new=AsyncMock(return_value=99)),
    ):
        res = await account_link.link_vk(
            "ABC123", 555, display_name="Пётр", group_id=137760500, session_factory=lambda: fake
        )
    assert res["status"] == "linked"
    out = fake.added[0]
    assert out.type == "vk_dm"
    assert out.target == "555"
    assert out.user_id == 7
    assert out.config == {"group_id": 137760500}
    assert out.last_item_id == 99


@pytest.mark.asyncio
async def test_link_vk_rejects_telegram_code():
    # Код, выписанный для telegram, нельзя использовать для VK-привязки.
    with patch.object(account_link, "resolve_link_code", return_value=("telegram", 7)):
        res = await account_link.link_vk("ABC", 1, session_factory=lambda: _LinkSession())
    assert res["status"] == "invalid"
