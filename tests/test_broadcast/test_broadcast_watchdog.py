"""Тесты watchdog #018 сетевой рассылки (maybe_alert_stale_broadcast)."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

from modules.broadcast import dispatcher as d


def _run():
    return asyncio.run(d.maybe_alert_stale_broadcast(telegram_token="t", chat_id="c"))


def test_no_overdue_is_silent():
    with patch.object(d, "_has_overdue_campaigns", new=AsyncMock(return_value=False)):
        assert _run() == "no-overdue"


def test_overdue_without_telegram_skips():
    with patch.object(d, "_has_overdue_campaigns", new=AsyncMock(return_value=True)):
        status = asyncio.run(d.maybe_alert_stale_broadcast(telegram_token=None, chat_id=None))
    assert status == "skipped:no-telegram-config"


def test_overdue_alerts_once():
    redis = MagicMock()
    redis.get.side_effect = [None, str(int(time.time()) - 600)]  # cooldown пуст, затем heartbeat
    with (
        patch.object(d, "_has_overdue_campaigns", new=AsyncMock(return_value=True)),
        patch.object(d, "_redis", return_value=redis),
        patch("requests.post") as post,
    ):
        post.return_value = MagicMock(status_code=200)
        status = _run()
    assert status == "alert-sent"
    post.assert_called_once()
    redis.setex.assert_called_once()  # cooldown взведён


def test_overdue_under_cooldown_silent():
    redis = MagicMock()
    redis.get.return_value = "1"  # cooldown активен
    with (
        patch.object(d, "_has_overdue_campaigns", new=AsyncMock(return_value=True)),
        patch.object(d, "_redis", return_value=redis),
        patch("requests.post") as post,
    ):
        status = _run()
    assert status == "skipped:cooldown"
    post.assert_not_called()
