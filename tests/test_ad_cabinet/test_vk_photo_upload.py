"""Тесты загрузки офферных картинок в ЛС VK."""

from __future__ import annotations

from unittest.mock import MagicMock

import modules.ad_cabinet.vk_photo_upload as vpu


def _resp(payload):
    r = MagicMock()
    r.json.return_value = payload
    return r


def test_upload_message_photo_returns_attachment(monkeypatch):
    api = MagicMock()
    api.photos.getMessagesUploadServer.return_value = {"upload_url": "http://up"}
    api.photos.saveMessagesPhoto.return_value = [{"owner_id": -5, "id": 99}]
    monkeypatch.setattr(
        vpu.requests,
        "post",
        lambda *a, **k: _resp({"server": 1, "photo": "p", "hash": "h"}),
    )
    att = vpu.upload_message_photo(api, b"bytes", peer_id=42)
    assert att == "photo-5_99"


def test_upload_message_photo_empty_returns_none(monkeypatch):
    api = MagicMock()
    api.photos.getMessagesUploadServer.return_value = {"upload_url": "http://up"}
    monkeypatch.setattr(
        vpu.requests,
        "post",
        lambda *a, **k: _resp({"server": 1, "photo": "[]", "hash": "h"}),
    )
    assert vpu.upload_message_photo(api, b"x", peer_id=42) is None


def test_upload_offer_images_caps_at_5(monkeypatch):
    api = MagicMock()
    api.photos.getMessagesUploadServer.return_value = {"upload_url": "http://up"}
    counter = {"n": 0}

    def _save(*a, **k):
        counter["n"] += 1
        return [{"owner_id": -5, "id": counter["n"]}]

    api.photos.saveMessagesPhoto.side_effect = _save
    monkeypatch.setattr(
        vpu.requests,
        "post",
        lambda *a, **k: _resp({"server": 1, "photo": "p", "hash": "h"}),
    )
    out = vpu.upload_offer_images(api, [b"x"] * 8, peer_id=42)
    assert out.count("photo") == 5
