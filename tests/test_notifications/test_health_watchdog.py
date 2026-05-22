"""Tests for the token-health watchdog (etap 5)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from modules.notifications.health import (
    ZERO_STREAK_THRESHOLD,
    detect_zero_streaks,
    maybe_alert_broken_tokens,
)


def _storage_with_runs(runs_by_type):
    storage = MagicMock()
    storage.key_prefix = "setka:notifications"
    storage.redis_client = MagicMock()
    storage.redis_client.get.return_value = None  # no cooldown active

    def fake_get(ntype, *_args, **_kwargs):
        return runs_by_type.get(ntype, [])

    storage.get_recent_runs.side_effect = fake_get
    return storage


def test_detect_streaks_no_zero_when_latest_has_results():
    storage = _storage_with_runs(
        {
            "suggested_posts": [{"count": 2}, {"count": 0}, {"count": 0}],
            "unread_messages": [],
            "recent_comments": [],
        }
    )
    streaks = detect_zero_streaks(storage)
    assert streaks["suggested_posts"] == 0


def test_detect_streaks_counts_leading_zeros():
    storage = _storage_with_runs(
        {
            "suggested_posts": [{"count": 0}, {"count": 0}, {"count": 0}, {"count": 1}],
            "unread_messages": [{"count": 0}],
            "recent_comments": [],
        }
    )
    streaks = detect_zero_streaks(storage)
    assert streaks["suggested_posts"] == 3
    assert streaks["unread_messages"] == 1
    assert streaks["recent_comments"] == 0


@pytest.mark.asyncio
async def test_alert_not_sent_below_threshold():
    storage = _storage_with_runs(
        {
            "suggested_posts": [{"count": 0}, {"count": 0}],
            "unread_messages": [],
            "recent_comments": [],
        }
    )
    status = await maybe_alert_broken_tokens(
        storage=storage,
        telegram_token="X",
        chat_id="Y",
        dashboard_url="https://example.test",
    )
    assert status == "no-alert"


@pytest.mark.asyncio
async def test_alert_skipped_during_cooldown():
    storage = _storage_with_runs(
        {
            "suggested_posts": [{"count": 0}] * (ZERO_STREAK_THRESHOLD + 1),
            "unread_messages": [],
            "recent_comments": [],
        }
    )
    storage.redis_client.get.return_value = "1"  # cooldown active

    status = await maybe_alert_broken_tokens(
        storage=storage,
        telegram_token="X",
        chat_id="Y",
        dashboard_url="https://example.test",
    )
    assert status == "skipped:cooldown"


@pytest.mark.asyncio
async def test_alert_sent_above_threshold():
    storage = _storage_with_runs(
        {
            "suggested_posts": [{"count": 0}] * (ZERO_STREAK_THRESHOLD + 1),
            "unread_messages": [{"count": 1}],
            "recent_comments": [],
        }
    )

    bot_instance = MagicMock()
    bot_instance.send_message = AsyncMock()

    with patch("telegram.Bot", return_value=bot_instance) as mock_bot_cls:
        status = await maybe_alert_broken_tokens(
            storage=storage,
            telegram_token="X",
            chat_id="Y",
            dashboard_url="https://example.test",
        )

    assert status == "alert-sent"
    mock_bot_cls.assert_called_once_with(token="X")
    bot_instance.send_message.assert_awaited_once()
    storage.redis_client.setex.assert_called_once()
    cooldown_args = storage.redis_client.setex.call_args.args
    assert cooldown_args[0] == "setka:notifications:health_alert_cooldown"


@pytest.mark.asyncio
async def test_alert_skipped_if_no_telegram_config():
    storage = _storage_with_runs(
        {
            "suggested_posts": [{"count": 0}] * (ZERO_STREAK_THRESHOLD + 1),
        }
    )
    status = await maybe_alert_broken_tokens(storage=storage)
    assert status == "skipped:no-telegram-config"
