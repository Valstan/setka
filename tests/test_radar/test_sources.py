"""Tests for modules/radar/sources — адаптеры VK/RSS и диспетчер (Ф0.2)."""

from datetime import datetime
from types import SimpleNamespace

import pytest

from modules.radar.sources import FetchedItem, get_fetcher
from modules.radar.sources.rss import parse_feed_bytes
from modules.radar.sources.vk import _media_from_attachments, _parse_vk_value


class TestGetFetcher:
    def test_vk_and_rss_supported(self):
        assert get_fetcher("vk") is not None
        assert get_fetcher("rss") is not None

    def test_tg_not_yet_supported(self):
        assert get_fetcher("tg") is None  # Ф0.3

    def test_unknown_type(self):
        assert get_fetcher("bogus") is None


class TestParseVkValue:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("-218688001", "-218688001"),
            ("218688001", "218688001"),
            ("club218688001", "-218688001"),
            ("public123", "-123"),
            ("event55", "-55"),
            ("id777", "777"),
            ("gonba_life", "gonba_life"),
            ("https://vk.com/gonba_life", "gonba_life"),
            ("http://m.vk.com/club123?w=wall-1_2", "-123"),
            ("vk.com/public123/", "-123"),
        ],
    )
    def test_variants(self, raw, expected):
        assert _parse_vk_value(raw) == expected

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            _parse_vk_value("https://vk.com/")


class TestVkMedia:
    def test_photo_picks_largest_size(self):
        post = {
            "attachments": [
                {
                    "type": "photo",
                    "photo": {
                        "sizes": [
                            {"width": 100, "height": 100, "url": "small"},
                            {"width": 1000, "height": 800, "url": "big"},
                        ]
                    },
                }
            ]
        }
        assert _media_from_attachments(post) == [{"type": "photo", "url": "big"}]

    def test_video_becomes_link(self):
        post = {"attachments": [{"type": "video", "video": {"owner_id": -1, "id": 42}}]}
        assert _media_from_attachments(post) == [
            {"type": "video", "url": "https://vk.com/video-1_42"}
        ]

    def test_unknown_attachment_ignored(self):
        assert _media_from_attachments({"attachments": [{"type": "doc"}]}) == []

    def test_no_attachments(self):
        assert _media_from_attachments({}) == []


RSS_SAMPLE = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Test Feed</title>
<item>
  <title>First post</title>
  <link>https://example.com/1</link>
  <guid>guid-1</guid>
  <description>Hello world</description>
  <pubDate>Wed, 11 Jun 2026 10:00:00 GMT</pubDate>
</item>
<item>
  <title>No guid post</title>
  <link>https://example.com/2</link>
</item>
</channel></rss>"""


class TestRssParse:
    def test_parses_entries(self):
        items = parse_feed_bytes(RSS_SAMPLE)
        assert len(items) == 2
        first = items[0]
        assert isinstance(first, FetchedItem)
        assert first.external_id == "guid-1"
        assert first.url == "https://example.com/1"
        assert first.title == "First post"
        assert first.text == "Hello world"
        assert first.published_at == datetime(2026, 6, 11, 10, 0, 0)

    def test_falls_back_to_link_as_id(self):
        items = parse_feed_bytes(RSS_SAMPLE)
        assert items[1].external_id == "https://example.com/2"
        assert items[1].published_at is None

    def test_garbage_bytes_yield_empty(self):
        assert parse_feed_bytes(b"not xml at all") == []


@pytest.mark.asyncio
async def test_vk_fetch_new_normalizes_posts(monkeypatch):
    from modules.radar.sources import vk as vk_adapter

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get_wall_posts(self, owner_id, count=20):
            assert owner_id == -218688001
            return [
                {"id": 5, "text": " hello ", "date": 1750000000},
                {"no_id": True},
            ]

    monkeypatch.setattr("modules.vk_monitor.vk_client_async.VKClientAsync", FakeClient)
    monkeypatch.setattr("config.runtime.VK_TOKENS", {"VALSTAN": "tok"})

    source = SimpleNamespace(key="-218688001", type="vk")
    items = await vk_adapter.fetch_new(source)
    assert len(items) == 1
    assert items[0].external_id == "5"
    assert items[0].url == "https://vk.com/wall-218688001_5"
    assert items[0].text == "hello"
    assert items[0].published_at == datetime.utcfromtimestamp(1750000000)
