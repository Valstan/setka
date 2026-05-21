"""Tests for keep_if_empty behaviour in NotificationsStorage.

Scenario: a manual /check-now run from UI found 4 suggested posts. One hour
later the automatic Celery task returned 0 (community-tokens broken).
Without keep_if_empty the UI would show 0; with keep_if_empty=True we keep
the previous result while it's young (default 6h window).
"""
import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from modules.notifications.storage import NotificationsStorage


def _make_storage():
    with patch("modules.notifications.storage.redis.Redis") as r:
        r.return_value = MagicMock()
        storage = NotificationsStorage()
    return storage


def test_keep_if_empty_preserves_recent_non_empty():
    storage = _make_storage()
    prev_ts = datetime.now().isoformat()
    existing = json.dumps({
        "timestamp": prev_ts,
        "notifications": [{"region_id": 1, "suggested_count": 4}],
    })
    storage.redis_client.get.return_value = existing

    saved = storage.save_notifications(
        [],
        notification_type="suggested_posts",
        keep_if_empty=True,
    )

    assert saved is False  # запись задержана
    storage.redis_client.setex.assert_not_called()


def test_keep_if_empty_replaces_old_non_empty():
    """If previous result is older than keep_window_hours — replace it."""
    storage = _make_storage()
    old_ts = (datetime.now() - timedelta(hours=10)).isoformat()
    existing = json.dumps({
        "timestamp": old_ts,
        "notifications": [{"region_id": 1, "suggested_count": 4}],
    })
    storage.redis_client.get.return_value = existing

    saved = storage.save_notifications(
        [],
        notification_type="suggested_posts",
        keep_if_empty=True,
        keep_window_hours=6,
    )

    assert saved is True
    storage.redis_client.setex.assert_called_once()


def test_keep_if_empty_writes_when_no_previous():
    """If there's no previous Redis key, write the empty result normally."""
    storage = _make_storage()
    storage.redis_client.get.return_value = None

    saved = storage.save_notifications(
        [],
        notification_type="suggested_posts",
        keep_if_empty=True,
    )

    assert saved is True
    storage.redis_client.setex.assert_called_once()


def test_non_empty_result_always_writes():
    """Any non-empty result overwrites whatever was there, regardless of flag."""
    storage = _make_storage()
    storage.redis_client.get.return_value = json.dumps({
        "timestamp": datetime.now().isoformat(),
        "notifications": [{"region_id": 5}],
    })

    saved = storage.save_notifications(
        [{"region_id": 1}],
        notification_type="suggested_posts",
        keep_if_empty=True,
    )

    assert saved is True
    storage.redis_client.setex.assert_called_once()


def test_default_behaviour_unchanged():
    """Without keep_if_empty (legacy callers) save always overwrites."""
    storage = _make_storage()
    storage.redis_client.get.return_value = json.dumps({
        "timestamp": datetime.now().isoformat(),
        "notifications": [{"region_id": 5}],
    })

    saved = storage.save_notifications([], notification_type="suggested_posts")

    assert saved is True
    storage.redis_client.setex.assert_called_once()


def test_keep_window_with_corrupt_timestamp():
    """If previous entry has unparseable timestamp, treat it as 'old' and overwrite."""
    storage = _make_storage()
    storage.redis_client.get.return_value = json.dumps({
        "timestamp": "not-a-date",
        "notifications": [{"region_id": 1}],
    })

    saved = storage.save_notifications(
        [],
        notification_type="suggested_posts",
        keep_if_empty=True,
    )

    assert saved is True
    storage.redis_client.setex.assert_called_once()
