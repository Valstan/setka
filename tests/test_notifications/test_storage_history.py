"""Tests for run-history methods in NotificationsStorage (etap 3).

These power the "activity over 24h" widget on /notifications.
"""
import json
from unittest.mock import MagicMock, patch

from modules.notifications.storage import NotificationsStorage


def _make_storage():
    with patch("modules.notifications.storage.redis.Redis") as r:
        r.return_value = MagicMock()
        storage = NotificationsStorage()
    return storage


def test_save_run_lpushes_and_trims_and_expires():
    storage = _make_storage()
    pipe = storage.redis_client.pipeline.return_value

    ok = storage.save_run(
        "suggested_posts",
        count=3,
        duration_seconds=1.234,
        denied_count=0,
        success=True,
    )

    assert ok is True
    # The pipeline must call lpush + ltrim + expire (in any order, but all three)
    methods_called = {c[0] for c in pipe.method_calls}
    assert {"lpush", "ltrim", "expire"}.issubset(methods_called)
    pipe.execute.assert_called_once()

    # Inspect the JSON we pushed
    lpush_call = next(c for c in pipe.method_calls if c[0] == "lpush")
    key, payload = lpush_call.args
    assert key == "setka:notifications:history:suggested_posts"
    entry = json.loads(payload)
    assert entry["count"] == 3
    assert entry["duration_seconds"] == 1.234
    assert entry["success"] is True
    assert "ts" in entry


def test_get_recent_runs_decodes_json_list():
    storage = _make_storage()
    storage.redis_client.lrange.return_value = [
        '{"ts": "2026-05-21T13:00:00", "count": 4}',
        '{"ts": "2026-05-21T12:00:00", "count": 2}',
    ]

    runs = storage.get_recent_runs("suggested_posts")

    storage.redis_client.lrange.assert_called_once_with(
        "setka:notifications:history:suggested_posts", 0, 47
    )
    assert len(runs) == 2
    assert runs[0]["count"] == 4
    assert runs[1]["count"] == 2


def test_get_stats_aggregates_three_types():
    storage = _make_storage()

    runs_by_key = {
        "setka:notifications:history:suggested_posts": [
            '{"ts":"2026-05-21T13:00","count":4,"duration_seconds":2.0}',
            '{"ts":"2026-05-21T12:00","count":0,"duration_seconds":2.5}',
            '{"ts":"2026-05-21T11:00","count":1,"duration_seconds":1.5}',
        ],
        "setka:notifications:history:unread_messages": [
            '{"ts":"2026-05-21T13:00","count":0,"duration_seconds":8.0}',
        ],
        "setka:notifications:history:recent_comments": [],
    }
    storage.redis_client.lrange.side_effect = lambda k, *_: runs_by_key.get(k, [])

    stats = storage.get_stats()

    assert stats["window_hours"] == 24
    sp = stats["types"]["suggested_posts"]
    assert sp["total_runs"] == 3
    assert sp["with_results_runs"] == 2
    assert sp["total_items"] == 5
    assert sp["avg_duration_s"] == 2.0
    assert sp["last_run_count"] == 4
    msg = stats["types"]["unread_messages"]
    assert msg["total_runs"] == 1
    assert msg["with_results_runs"] == 0
    cmt = stats["types"]["recent_comments"]
    assert cmt["total_runs"] == 0
    assert cmt["last_run_ts"] is None


def test_save_run_with_extra_payload():
    storage = _make_storage()
    pipe = storage.redis_client.pipeline.return_value

    storage.save_run(
        "unread_messages",
        count=2,
        duration_seconds=8.5,
        denied_count=14,
        success=True,
        extra={"via": "community-fallback-user"},
    )

    lpush_call = next(c for c in pipe.method_calls if c[0] == "lpush")
    entry = json.loads(lpush_call.args[1])
    assert entry["denied_count"] == 14
    assert entry["extra"] == {"via": "community-fallback-user"}
