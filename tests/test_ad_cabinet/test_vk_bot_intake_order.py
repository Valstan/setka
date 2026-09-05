"""Чистые куски intake бота (Этап 5): черновик → аргументы заказа, скачивание фото.

``order_kwargs`` отдаёт basename'ы фото (как кладёт кабинет) и не больше лимита
на пост; ``make_photo_fetch`` отдаёт байты только для картинок в пределах веса и
никогда не бросает.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from modules.ad_cabinet import client_photos
from modules.ad_cabinet.vk_bot import intake


def test_order_kwargs_maps_draft():
    kw = intake.order_kwargs(
        {
            "text": "t",
            "photos": ["a.jpg", "../b.jpg", ""],
            "region_ids": [1, 2],
            "publish_at": "2026-09-02T12:00:00",
            "publish_now": False,
        }
    )
    assert kw == {
        "text": "t",
        "image_paths": ["a.jpg", "b.jpg"],
        "region_ids": [1, 2],
        "publish_at": datetime(2026, 9, 2, 12, 0),
        "publish_now": False,
    }


def test_order_kwargs_defaults_and_photo_cap():
    kw = intake.order_kwargs({"photos": [f"{i}.jpg" for i in range(15)], "publish_now": True})
    assert kw["text"] == "" and kw["publish_at"] is None and kw["publish_now"] is True
    assert len(kw["image_paths"]) == client_photos.MAX_PHOTOS_PER_POST
    assert intake.order_kwargs({})["image_paths"] == []


class _Resp:
    def __init__(self, content=b"", ctype="image/jpeg", status=200):
        self.content = content
        self.headers = {"content-type": ctype}
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


class _Client:
    """Двойник httpx.AsyncClient: отвечает по сценарию, запоминает URL."""

    plan = {}
    seen = []

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url):
        _Client.seen.append(url)
        r = _Client.plan[url]
        if isinstance(r, Exception):
            raise r
        return r


@pytest.mark.asyncio
async def test_make_photo_fetch_filters(monkeypatch):
    monkeypatch.setattr(intake.httpx, "AsyncClient", _Client)
    _Client.plan = {
        "ok": _Resp(b"\xff\xd8\xff"),
        "html": _Resp(b"<html>", ctype="text/html"),
        "big": _Resp(b"x" * (client_photos.MAX_IMG_BYTES + 1)),
        "empty": _Resp(b""),
        "http": _Resp(b"", status=404),
        "boom": ConnectionError("net"),
    }
    fetch = intake.make_photo_fetch(timeout=1)
    assert await fetch("ok") == b"\xff\xd8\xff"
    for u in ("html", "big", "empty", "http", "boom"):
        assert await fetch(u) is None, u


@pytest.mark.asyncio
async def test_make_photo_fetch_does_not_log_signed_url(monkeypatch, caplog):
    """403 от CDN: httpx.HTTPStatusError печатает полный URL — в лог уходит только тип и код."""
    import logging

    import httpx

    class _HttpClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            req = httpx.Request("GET", url)
            return httpx.Response(403, request=req, content=b"")

    monkeypatch.setattr(intake.httpx, "AsyncClient", _HttpClient)
    url = "https://sun9.userapi.com/x.jpg?size=604x604&sign=deadbeef"
    with caplog.at_level(logging.WARNING, logger=intake.logger.name):
        assert await intake.make_photo_fetch(timeout=1)(url) is None
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "photo fetch failed" in text and "HTTPStatusError" in text and "403" in text
    assert "sign=" not in text and "?" not in text
