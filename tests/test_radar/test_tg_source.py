"""Tests for modules/radar/sources/tg.py — парсер t.me/s, резолв, relay (Ф0.3)."""

import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from modules.radar.sources import get_fetcher
from modules.radar.sources import tg as tg_adapter
from modules.radar.sources.tg import (
    parse_channel_value,
    parse_messages,
    relay_media_url,
    resolve_source,
)

SAMPLE_HTML = """
<div class="tgme_widget_message_wrap">
 <div class="tgme_widget_message" data-post="gonba_life/3721">
  <div class="tgme_widget_message_text js-message_text">Привет, <b>село</b>!<br/>Вторая строка</div>
  <a class="tgme_widget_message_photo_wrap blured js-message_photo"
     style="width:453px;background-image:url('https://cdn4.telesco.pe/file/AAA.jpg')"></a>
  <time datetime="2026-06-10T06:40:08+00:00" class="time">06:40</time>
 </div>
</div>
<div class="tgme_widget_message_wrap">
 <div class="tgme_widget_message" data-post="gonba_life/3722">
  <div class="tgme_widget_message_video_player">…</div>
  <time datetime="2026-06-11T10:00:00+00:00" class="time">10:00</time>
 </div>
</div>
"""


class TestParseChannelValue:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("gonba_life", "gonba_life"),
            ("@gonba_life", "gonba_life"),
            ("t.me/gonba_life", "gonba_life"),
            ("https://t.me/s/gonba_life?before=3", "gonba_life"),
            ("telegram.me/tass_agency", "tass_agency"),
        ],
    )
    def test_variants(self, raw, expected):
        assert parse_channel_value(raw) == expected

    @pytest.mark.parametrize("bad", ["", "@", "ab", "канал", "a b", "x" * 65])
    def test_invalid(self, bad):
        with pytest.raises(ValueError):
            parse_channel_value(bad)


class TestParseMessages:
    def test_extracts_messages(self):
        msgs = parse_messages(SAMPLE_HTML)
        assert [m["id"] for m in msgs] == [3721, 3722]
        first = msgs[0]
        assert first["text"] == "Привет, село!\nВторая строка"
        assert first["photos"] == ["https://cdn4.telesco.pe/file/AAA.jpg"]
        assert first["has_video"] is False
        assert first["published_at"] == datetime(2026, 6, 10, 6, 40, 8)
        assert msgs[1]["has_video"] is True
        assert msgs[1]["text"] is None

    def test_grouped_media_dedupes_anchor(self):
        html = SAMPLE_HTML + '<a data-post="gonba_life/3721" class="grouped"></a>'
        assert len(parse_messages(html)) == 2

    def test_empty_page(self):
        assert parse_messages("<html>nothing</html>") == []


@pytest.fixture
def _relay_env(monkeypatch):
    monkeypatch.setenv("TG_PREVIEW_RELAY_URL", "https://relay.example")
    monkeypatch.setenv("TG_RELAY_SECRET", "s3cret")


def _fake_response(text="", status=200, headers=None):
    response = MagicMock()
    response.text = text
    response.status_code = status
    response.headers = headers or {}
    return response


def _fake_client(response):
    client = MagicMock()
    client.get = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


def test_relay_media_url(_relay_env):
    url = relay_media_url("https://cdn4.telesco.pe/file/A B.jpg")
    assert url == "https://relay.example/media?u=https%3A%2F%2Fcdn4.telesco.pe%2Ffile%2FA%20B.jpg"


def test_relay_media_url_without_config(monkeypatch):
    monkeypatch.delenv("TG_PREVIEW_RELAY_URL", raising=False)
    monkeypatch.delenv("TG_RELAY_SECRET", raising=False)
    assert relay_media_url("https://cdn4.telesco.pe/x.jpg") is None


@pytest.mark.asyncio
async def test_fetch_new_parses_relay_json(_relay_env):
    body = json.dumps(SAMPLE_HTML)  # relay отдаёт AJAX-формат: JSON-строка с HTML
    with patch("httpx.AsyncClient", return_value=_fake_client(_fake_response(text=body))):
        items = await tg_adapter.fetch_new(SimpleNamespace(key="gonba_life", type="tg"))
    assert len(items) == 2
    assert items[0].external_id == "3721"
    assert items[0].url == "https://t.me/gonba_life/3721"
    assert items[0].media[0]["url"].startswith("https://cdn4.telesco.pe/")
    assert items[1].media == [{"type": "video", "url": "https://t.me/gonba_life/3722"}]


@pytest.mark.asyncio
async def test_fetch_new_redirect_means_dead_channel(_relay_env):
    response = _fake_response(status=302, headers={"x-relay-redirect": "https://t.me/x"})
    with patch("httpx.AsyncClient", return_value=_fake_client(response)):
        with pytest.raises(RuntimeError, match="redirects"):
            await tg_adapter.fetch_new(SimpleNamespace(key="deadchan", type="tg"))


@pytest.mark.asyncio
async def test_fetch_new_without_relay_config(monkeypatch):
    monkeypatch.delenv("TG_PREVIEW_RELAY_URL", raising=False)
    monkeypatch.delenv("TG_RELAY_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="not configured"):
        await tg_adapter.fetch_new(SimpleNamespace(key="ch_name", type="tg"))


@pytest.mark.asyncio
async def test_resolve_source_dead_channel_is_value_error(_relay_env):
    response = _fake_response(status=302, headers={"x-relay-redirect": "https://t.me/x"})
    with patch("httpx.AsyncClient", return_value=_fake_client(response)):
        with pytest.raises(ValueError, match="веб-превью"):
            await resolve_source("@deadchan")


@pytest.mark.asyncio
async def test_resolve_source_timeout_retries_then_clear_error(_relay_env):
    import httpx

    client = MagicMock()
    client.get = AsyncMock(side_effect=httpx.ReadTimeout("timed out"))
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    with patch("httpx.AsyncClient", return_value=client):
        with pytest.raises(ValueError, match="ещё раз"):
            await resolve_source("@bigchannel")
    # ретраил RELAY_RETRIES раз
    assert client.get.await_count == tg_adapter.RELAY_RETRIES


@pytest.mark.asyncio
async def test_resolve_source_empty_feed_is_value_error(_relay_env):
    # 200, но нет ни og:title, ни data-post → веб-превью пустое/выключено.
    with patch(
        "httpx.AsyncClient", return_value=_fake_client(_fake_response(text="<html></html>"))
    ):
        with pytest.raises(ValueError, match="нет доступной ленты"):
            await resolve_source("@emptychan")


@pytest.mark.asyncio
async def test_resolve_source_title_from_owner_name(_relay_env):
    html = (
        '<a class="tgme_widget_message_owner_name" href="x"><span dir="auto">'
        "Гоньба — жемчужина</span></a>" + SAMPLE_HTML
    )
    # og:title в AJAX-фрагменте нет — берём owner_name.
    html = html.replace('class="tgme_widget_message_owner_name"[^>]*>', "")
    with patch(
        "httpx.AsyncClient", return_value=_fake_client(_fake_response(text=json.dumps(html)))
    ):
        meta = await resolve_source("t.me/gonba_life")
    assert meta["key"] == "gonba_life"
    assert meta["url"] == "https://t.me/gonba_life"
    assert "Гоньба" in meta["title"]


def test_get_fetcher_supports_tg():
    assert get_fetcher("tg") is tg_adapter.fetch_new
