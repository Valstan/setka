"""Библиотека фото клиента (modules/ad_cabinet/client_photos) — Этап 5.

Что охраняется: порядок и тексты проверок те же, что отдавал кабинет
(суффикс → пусто → вес → лимит 20 → диск), файл кладётся в ``<root>/<client_id>/``,
удаление не выходит из каталога, разбор вложений ВК берёт самую крупную копию
и пропускает не-фото.
"""

from __future__ import annotations

import pytest

from modules.ad_cabinet import client_photos as cp

JPG = b"\xff\xd8\xff\xe0" + b"x" * 16


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setenv("AD_UPLOAD_DIR", str(tmp_path))
    monkeypatch.setenv("AD_UPLOAD_MIN_FREE_BYTES", "0")
    return tmp_path


def test_store_writes_file_under_client_dir(store):
    name = cp.store_client_photo(7, JPG, ".jpg")
    assert len(name) == 36 and name.endswith(".jpg")
    assert (store / "7" / name).read_bytes() == JPG
    assert [p.name for p in cp.client_photo_paths(7)] == [name]


@pytest.mark.parametrize(
    "data,suffix,status,text",
    [
        (JPG, ".gif", 400, "Только JPG/PNG"),
        (b"", ".jpg", 400, "Пустой файл"),
        (b"x" * (cp.MAX_IMG_BYTES + 1), ".png", 400, "больше 12 МБ"),
    ],
    ids=["gif", "empty", "oversize"],
)
def test_store_rejects_bad_input(store, data, suffix, status, text):
    with pytest.raises(cp.PhotoError) as ei:
        cp.store_client_photo(7, data, suffix)
    assert ei.value.status == status and text in ei.value.detail
    # отказ до записи: каталог клиента либо не создан, либо пуст
    assert not (store / "7").exists() or not list((store / "7").iterdir())


def test_store_respects_client_limit(store):
    d = store / "7"
    d.mkdir()
    for i in range(cp.MAX_PHOTOS_PER_CLIENT):
        (d / f"{i:02d}.jpg").write_bytes(JPG)
    with pytest.raises(cp.PhotoError) as ei:
        cp.store_client_photo(7, JPG, ".jpg")
    assert f"Лимит {cp.MAX_PHOTOS_PER_CLIENT}" in ei.value.detail
    assert len(list(d.iterdir())) == cp.MAX_PHOTOS_PER_CLIENT


def test_store_checks_free_space_last(store, monkeypatch):
    monkeypatch.setenv("AD_UPLOAD_MIN_FREE_BYTES", "1000")

    class _Usage:
        def __init__(self, free):
            self.free = free

    monkeypatch.setattr(cp.shutil, "disk_usage", lambda p: _Usage(free=500))
    with pytest.raises(cp.PhotoError) as ei:
        cp.store_client_photo(7, JPG, ".jpg")
    assert ei.value.status == 507
    # диск недоступен для замера → считаем, что влезает
    monkeypatch.setattr(cp.shutil, "disk_usage", lambda p: (_ for _ in ()).throw(OSError()))
    assert cp.fits_disk(10) is True


def test_remove_only_listed_basenames(store):
    a = cp.store_client_photo(7, JPG, ".jpg")
    b = cp.store_client_photo(7, JPG, ".jpg")
    (store / "outside.jpg").write_bytes(JPG)
    removed = cp.remove_client_photos(7, [a, "../outside.jpg", "", "nope.jpg"])
    assert removed == 1
    assert [p.name for p in cp.client_photo_paths(7)] == [b]
    assert (store / "outside.jpg").exists()  # из каталога клиента не вышли


def test_photo_urls_take_largest_and_skip_non_photos():
    att = [
        {
            "type": "photo",
            "photo": {
                "sizes": [
                    {"type": "m", "width": 130, "url": "u-small"},
                    {"type": "x", "width": 604, "url": "u-big"},
                    {"type": "s", "url": "u-nowidth"},
                ]
            },
        },
        {"type": "doc", "doc": {"url": "d"}},
        {"type": "photo", "photo": {"sizes": []}},
        "мусор",
        {"type": "photo", "photo": {"sizes": [{"width": 10, "url": "u2"}]}},
    ]
    assert cp.photo_urls_from_attachments(att) == ["u-big", "u2"]
    assert cp.photo_urls_from_attachments(att, limit=1) == ["u-big"]
    assert cp.photo_urls_from_attachments([]) == []


def test_upload_root_defaults_to_web_uploads(monkeypatch):
    monkeypatch.setenv("AD_UPLOAD_DIR", "")
    assert cp.upload_root().parts[-3:] == ("web", "uploads", "advertiser")


def test_evict_oldest_respects_keep(store):
    import os
    import time

    d = store / "7"
    d.mkdir()
    base = time.time() - 1000
    for i in range(4):
        p = d / f"{i}.jpg"
        p.write_bytes(JPG)
        os.utime(p, (base + i, base + i))
    assert cp.evict_oldest(7, {"0.jpg"}, 2) == ["1.jpg", "2.jpg"]
    assert sorted(p.name for p in cp.client_photo_paths(7)) == ["0.jpg", "3.jpg"]
    assert cp.evict_oldest(7, set(), 0) == []


@pytest.mark.asyncio
async def test_cabinet_upload_maps_photo_errors(store, monkeypatch, db_session):
    """POST /api/advertiser/photos — тонкая обёртка: 400 «Пустой файл», 507 «мало места»."""
    from fastapi import HTTPException

    from database.models import AdClient
    from web.api import advertiser_cabinet as ac

    client = AdClient(name="К")
    db_session.add(client)
    await db_session.flush()

    async def current(request, db):
        return None, client

    monkeypatch.setattr(ac, "_current_client", current)

    class _Up:
        def __init__(self, filename, data):
            self.filename = filename
            self._data = data

        async def read(self):
            return self._data

    with pytest.raises(HTTPException) as ei:
        await ac.upload_photo(request=None, file=_Up("a.jpg", b""), db=db_session)
    assert ei.value.status_code == 400 and "Пустой файл" in ei.value.detail
    monkeypatch.setenv("AD_UPLOAD_MIN_FREE_BYTES", str(10**18))
    with pytest.raises(HTTPException) as ei:
        await ac.upload_photo(request=None, file=_Up("a.jpg", JPG), db=db_session)
    assert ei.value.status_code == 507
    monkeypatch.setenv("AD_UPLOAD_MIN_FREE_BYTES", "0")
    out = await ac.upload_photo(request=None, file=_Up("a.jpg", JPG), db=db_session)
    assert out["name"].endswith(".jpg") and (store / str(client.id) / out["name"]).exists()
