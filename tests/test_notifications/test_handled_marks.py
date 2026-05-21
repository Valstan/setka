"""Tests for handled-mark storage methods (etap 4a)."""
from unittest.mock import MagicMock, patch

from modules.notifications.storage import NotificationsStorage


def _make_storage():
    with patch("modules.notifications.storage.redis.Redis") as r:
        r.return_value = MagicMock()
        storage = NotificationsStorage()
    return storage


def test_mark_handled_sets_redis_key_with_ttl():
    storage = _make_storage()
    ok = storage.mark_handled("recent_comment", 42)
    assert ok is True
    storage.redis_client.setex.assert_called_once()
    args = storage.redis_client.setex.call_args.args
    assert args[0] == "setka:notifications:handled:recent_comment:42"
    assert args[1] == NotificationsStorage.HANDLED_TTL_SECONDS


def test_unmark_handled_deletes_key():
    storage = _make_storage()
    storage.unmark_handled("recent_comment", 42)
    storage.redis_client.delete.assert_called_once_with(
        "setka:notifications:handled:recent_comment:42"
    )


def test_is_handled_returns_bool():
    storage = _make_storage()
    storage.redis_client.exists.return_value = 1
    assert storage.is_handled("recent_comment", 42) is True
    storage.redis_client.exists.return_value = 0
    assert storage.is_handled("recent_comment", 42) is False


def test_get_handled_set_strips_prefix():
    storage = _make_storage()
    storage.redis_client.keys.return_value = [
        "setka:notifications:handled:recent_comment:42",
        "setka:notifications:handled:recent_comment:99",
    ]
    result = storage.get_handled_set("recent_comment")
    assert result == {"42", "99"}
