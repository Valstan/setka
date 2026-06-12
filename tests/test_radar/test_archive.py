"""Tests for modules/radar/archive.py — скачивание медиа, квота, пути (Ф0.4)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from modules.radar import archive


@pytest.fixture(autouse=True)
def _tmp_archive_root(tmp_path, monkeypatch):
    monkeypatch.setenv("RADAR_ARCHIVE_DIR", str(tmp_path))
    return tmp_path


class TestExtFor:
    def test_by_content_type(self):
        assert archive._ext_for("image/jpeg", "x") == ".jpg"
        assert archive._ext_for("image/png; charset=binary", "x") == ".png"

    def test_by_url_fallback(self):
        assert archive._ext_for("", "https://e.com/a.JPG?size=1") == ".jpg"

    def test_unknown(self):
        assert archive._ext_for("application/octet-stream", "https://e.com/a") == ".bin"


class TestMediaFilePath:
    def test_traversal_rejected(self):
        assert archive.media_file_path(1, 2, "../secret") is None
        assert archive.media_file_path(1, 2, "a/b.jpg") is None
        assert archive.media_file_path(1, 2, "..\\x") is None

    def test_missing_file_none(self):
        assert archive.media_file_path(1, 2, "00.jpg") is None

    def test_existing_file_found(self, _tmp_archive_root):
        d = _tmp_archive_root / "1" / "2"
        d.mkdir(parents=True)
        (d / "00.jpg").write_bytes(b"x")
        path = archive.media_file_path(1, 2, "00.jpg")
        assert path is not None and path.read_bytes() == b"x"


def _fake_httpx_client(blob=b"JPEGDATA", status_ok=True):
    response = MagicMock()
    response.content = blob
    response.headers = {"content-type": "image/jpeg"}
    if not status_ok:
        response.raise_for_status.side_effect = RuntimeError("404")
    client = MagicMock()
    client.get = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


@pytest.mark.asyncio
async def test_download_media_writes_photo(_tmp_archive_root):
    media = [
        {"type": "photo", "url": "https://e.com/p.jpg"},
        {"type": "video", "url": "https://vk.com/video-1_2"},
    ]
    with patch("httpx.AsyncClient", return_value=_fake_httpx_client()):
        result, downloaded = await archive.download_media(media, 1, 2, quota_left=10**6)

    assert downloaded == len(b"JPEGDATA")
    assert result[0]["file"] == "00.jpg"
    assert result[0]["bytes"] == downloaded
    assert (_tmp_archive_root / "1" / "2" / "00.jpg").read_bytes() == b"JPEGDATA"
    # Видео — всегда ссылкой, без файла.
    assert result[1] == {"type": "video", "url": "https://vk.com/video-1_2"}


@pytest.mark.asyncio
async def test_download_media_respects_quota(_tmp_archive_root):
    media = [{"type": "photo", "url": "https://e.com/p.jpg"}]
    with patch("httpx.AsyncClient", return_value=_fake_httpx_client(blob=b"x" * 100)):
        result, downloaded = await archive.download_media(media, 1, 2, quota_left=50)

    assert downloaded == 0
    assert "file" not in result[0]  # не влезло — осталось ссылкой
    assert result[0]["url"] == "https://e.com/p.jpg"


@pytest.mark.asyncio
async def test_download_media_failure_keeps_link(_tmp_archive_root):
    media = [{"type": "photo", "url": "https://e.com/p.jpg"}]
    with patch("httpx.AsyncClient", return_value=_fake_httpx_client(status_ok=False)):
        result, downloaded = await archive.download_media(media, 1, 2, quota_left=10**6)
    assert downloaded == 0
    assert result == [{"type": "photo", "url": "https://e.com/p.jpg"}]


class TestDownloadPlan:
    def test_tg_cdn_goes_through_relay(self, monkeypatch):
        monkeypatch.setenv("TG_PREVIEW_RELAY_URL", "https://relay.example")
        monkeypatch.setenv("TG_RELAY_SECRET", "s3cret")
        url, headers = archive._download_plan("https://cdn4.telesco.pe/file/A.jpg")
        assert url.startswith("https://relay.example/media?u=")
        assert headers == {"X-Relay-Secret": "s3cret"}

    def test_tg_cdn_without_relay_stays_direct(self, monkeypatch):
        monkeypatch.delenv("TG_PREVIEW_RELAY_URL", raising=False)
        monkeypatch.delenv("TG_RELAY_SECRET", raising=False)
        url, headers = archive._download_plan("https://cdn4.telesco.pe/file/A.jpg")
        assert url == "https://cdn4.telesco.pe/file/A.jpg"
        assert headers == {}

    def test_other_hosts_direct(self, monkeypatch):
        monkeypatch.setenv("TG_PREVIEW_RELAY_URL", "https://relay.example")
        monkeypatch.setenv("TG_RELAY_SECRET", "s3cret")
        url, headers = archive._download_plan("https://sun9-1.userapi.com/p.jpg")
        assert url == "https://sun9-1.userapi.com/p.jpg"
        assert headers == {}


def test_remove_saved_dir(_tmp_archive_root):
    d = _tmp_archive_root / "1" / "2"
    d.mkdir(parents=True)
    (d / "00.jpg").write_bytes(b"x")
    archive.remove_saved_dir(1, 2)
    assert not d.exists()
