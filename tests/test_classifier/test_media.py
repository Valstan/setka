"""Tests медиа-зрения классификатора: сводка вложений, снапшот аудита,
media_summary в вердикте, media-прокси (allowlist хостов)."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from modules.classifier.schema import ClassifierVerdict
from modules.curation.collection_audit import _snapshot
from utils.vk_attachments import summarize_media
from web.api import classifier_ingest as ing

KEY = "routine-secret"


# --- summarize_media -------------------------------------------------------


def _photo(url="https://sun9-1.userapi.com/img.jpg", w=1280):
    return {"type": "photo", "photo": {"sizes": [{"width": w, "url": url}]}}


def test_summarize_media_photo_best_size():
    post = {
        "attachments": [
            {
                "type": "photo",
                "photo": {
                    "sizes": [
                        {"width": 130, "url": "https://sun9-1.userapi.com/s.jpg"},
                        {"width": 1280, "url": "https://sun9-1.userapi.com/l.jpg"},
                    ]
                },
            }
        ]
    }
    media = summarize_media(post)
    assert media == [{"type": "photo", "url": "https://sun9-1.userapi.com/l.jpg"}]


def test_summarize_media_doc_video_and_cap():
    post = {
        "attachments": [
            {"type": "doc", "doc": {"url": "https://vk.com/doc1", "ext": "pdf", "title": "Афиша"}},
            {"type": "video", "video": {"title": "Ролик"}},
            {"type": "audio", "audio": {"title": "Песня"}},
        ]
    }
    media = summarize_media(post, max_items=2)
    assert len(media) == 2
    assert media[0] == {"type": "doc", "url": "https://vk.com/doc1", "ext": "pdf", "title": "Афиша"}
    assert media[1] == {"type": "video", "title": "Ролик"}


def test_summarize_media_empty():
    assert summarize_media({}) == []
    assert summarize_media({"attachments": []}) == []


# --- снапшот аудита несёт media --------------------------------------------


def test_audit_snapshot_captures_media():
    post = {"owner_id": -1, "id": 10, "text": "", "attachments": [_photo()]}
    snap = _snapshot(post, lip="1_10", region_code="mi", theme="novost", decision="kept", reason=None)
    assert snap["media"] == [{"type": "photo", "url": "https://sun9-1.userapi.com/img.jpg"}]


def test_audit_snapshot_media_none_when_empty():
    post = {"owner_id": -1, "id": 11, "text": "текст"}
    snap = _snapshot(post, lip="1_11", region_code="mi", theme="novost", decision="kept", reason=None)
    assert snap["media"] is None


# --- media_summary в вердикте ----------------------------------------------


def test_verdict_media_summary_serialized():
    v = ClassifierVerdict(lip="1_10", theme="kultura", media_summary="афиша концерта в ДК")
    assert v.to_verdict_json()["media_summary"] == "афиша концерта в ДК"


def test_verdict_media_summary_omitted_when_empty():
    v = ClassifierVerdict(lip="1_10", theme="kultura")
    assert "media_summary" not in v.to_verdict_json()


# --- media-прокси -----------------------------------------------------------


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("CLASSIFIER_INGEST_KEY", KEY)
    monkeypatch.delenv("CLASSIFIER_DISABLED", raising=False)


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(ing.router, prefix="/api/classifier")
    return TestClient(app)


def test_media_proxy_requires_key(client):
    r = client.get("/api/classifier/media", params={"url": "https://sun9-1.userapi.com/x.jpg"})
    assert r.status_code == 401


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example.com/x.jpg",  # чужой хост
        "http://sun9-1.userapi.com/x.jpg",  # не https
        "https://userapi.com.evil.com/x.jpg",  # суффикс-спуфинг
    ],
)
def test_media_proxy_rejects_bad_urls(client, url):
    r = client.get("/api/classifier/media", params={"url": url}, headers={"X-API-Key": KEY})
    assert r.status_code == 400


def test_media_url_allowed_hosts():
    assert ing._media_url_allowed("https://sun9-77.userapi.com/img.jpg")
    assert ing._media_url_allowed("https://vk.com/doc123")
    assert ing._media_url_allowed("https://psv4.vk.me/file.pdf")
    assert not ing._media_url_allowed("https://example.com/img.jpg")
    assert not ing._media_url_allowed("ftp://vk.com/x")


def test_media_proxy_streams_upstream(client):
    class _FakeResp:
        status_code = 200
        url = "https://sun9-1.userapi.com/img.jpg"
        headers = {"content-type": "image/jpeg"}

        async def aiter_bytes(self):
            yield b"JPEGDATA"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass

        def stream(self, method, url):
            return _FakeResp()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    with patch.object(ing.httpx, "AsyncClient", _FakeClient):
        r = client.get(
            "/api/classifier/media",
            params={"url": "https://sun9-1.userapi.com/img.jpg"},
            headers={"X-API-Key": KEY},
        )
    assert r.status_code == 200
    assert r.content == b"JPEGDATA"
    assert r.headers["content-type"].startswith("image/jpeg")
