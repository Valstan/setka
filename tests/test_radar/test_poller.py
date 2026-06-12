"""Tests for modules/radar/poller — fan-out, fail-isolation, watchdog (Ф0.2)."""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from database.models_extended import RadarSource
from modules.radar import poller
from modules.radar.sources import FetchedItem


class _FakeSession:
    """AsyncSessionLocal stand-in: select источников + insert элементов."""

    def __init__(self, sources, insert_rowcounts=None):
        self._sources = sources
        # Очередь rowcount'ов для INSERT'ов (1 = новый, 0 = дедуп-конфликт).
        self._insert_rowcounts = list(insert_rowcounts or [])
        self.inserts = 0
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, stmt):
        result = MagicMock()
        if stmt.__class__.__name__ == "Insert":
            self.inserts += 1
            result.rowcount = self._insert_rowcounts.pop(0) if self._insert_rowcounts else 1
        else:
            result.scalars.return_value.all.return_value = self._sources
        return result

    async def commit(self):
        self.committed = True


def _source(stype="vk", key="-1", **kw):
    src = RadarSource(type=stype, key=key, is_active=True, fail_count=0)
    src.id = kw.get("id", 1)
    return src


@pytest.mark.asyncio
async def test_poll_inserts_new_items_and_updates_meta():
    src = _source()

    async def fetch(_):
        return [
            FetchedItem(external_id="1", published_at=datetime(2026, 6, 12, 10)),
            FetchedItem(external_id="2", published_at=datetime(2026, 6, 12, 11)),
        ]

    fake = _FakeSession([src], insert_rowcounts=[1, 0])
    with (
        patch("database.connection.AsyncSessionLocal", return_value=fake),
        patch("modules.radar.sources.get_fetcher", return_value=fetch),
        patch.object(poller, "touch_heartbeat") as hb,
    ):
        summary = await poller.poll_all_sources()

    assert summary == {"sources": 1, "new_items": 1, "failed": 0}
    assert fake.inserts == 2  # оба поста ушли в INSERT, второй погашен ON CONFLICT
    assert src.fail_count == 0
    assert src.last_polled_at is not None
    assert src.last_item_at == datetime(2026, 6, 12, 11)
    assert fake.committed
    hb.assert_called_once()


@pytest.mark.asyncio
async def test_failed_source_does_not_break_run():
    bad, good = _source(id=1), _source(id=2, key="-2")

    async def fetch(source):
        if source is bad:
            raise RuntimeError("boom")
        return [FetchedItem(external_id="7")]

    fake = _FakeSession([bad, good])
    with (
        patch("database.connection.AsyncSessionLocal", return_value=fake),
        patch("modules.radar.sources.get_fetcher", return_value=fetch),
        patch.object(poller, "touch_heartbeat"),
    ):
        summary = await poller.poll_all_sources()

    assert summary == {"sources": 2, "new_items": 1, "failed": 1}
    assert bad.fail_count == 1
    assert "boom" in bad.last_error
    assert good.fail_count == 0


@pytest.mark.asyncio
async def test_unsupported_type_skipped_silently():
    # 'max' — мессенджер вне скоупа Ф0 (директива): фетчера нет, это не ошибка.
    unsupported = _source(stype="max")
    fake = _FakeSession([unsupported])
    with (
        patch("database.connection.AsyncSessionLocal", return_value=fake),
        patch.object(poller, "touch_heartbeat") as hb,
    ):
        summary = await poller.poll_all_sources()
    assert summary == {"sources": 0, "new_items": 0, "failed": 0}
    assert hb.called  # поллер жив, даже если поллить нечего


class TestWatchdog:
    @pytest.mark.asyncio
    async def test_no_sources_is_not_incident(self):
        with (
            patch.object(poller, "_redis", return_value=MagicMock()),
            patch.object(poller, "_has_pollable_sources", return_value=False),
        ):
            status = await poller.maybe_alert_stale_radar_poll(telegram_token="t", chat_id="c")
        assert status == "no-sources"

    @pytest.mark.asyncio
    async def test_fresh_heartbeat_ok(self):
        import time

        redis = MagicMock()
        redis.get.return_value = str(int(time.time()) - 60)
        with (
            patch.object(poller, "_redis", return_value=redis),
            patch.object(poller, "_has_pollable_sources", return_value=True),
        ):
            status = await poller.maybe_alert_stale_radar_poll(telegram_token="t", chat_id="c")
        assert status == "ok"

    @pytest.mark.asyncio
    async def test_stale_heartbeat_alerts_once(self):
        import time

        redis = MagicMock()
        # Первый get — heartbeat (протух), второй — cooldown (пуст).
        redis.get.side_effect = [str(int(time.time()) - 3600), None]
        with (
            patch.object(poller, "_redis", return_value=redis),
            patch.object(poller, "_has_pollable_sources", return_value=True),
            patch("requests.post") as post,
        ):
            status = await poller.maybe_alert_stale_radar_poll(telegram_token="t", chat_id="c")
        assert status == "stale-alerted"
        post.assert_called_once()
        redis.setex.assert_called_once()  # cooldown взведён

    @pytest.mark.asyncio
    async def test_stale_under_cooldown_silent(self):
        import time

        redis = MagicMock()
        redis.get.side_effect = [str(int(time.time()) - 3600), "1"]
        with (
            patch.object(poller, "_redis", return_value=redis),
            patch.object(poller, "_has_pollable_sources", return_value=True),
            patch("requests.post") as post,
        ):
            status = await poller.maybe_alert_stale_radar_poll(telegram_token="t", chat_id="c")
        assert status == "stale-cooldown"
        post.assert_not_called()
