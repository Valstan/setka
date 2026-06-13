"""Tests for web/api/radar.py — save-архив и курсор новизны (Ф0.4)."""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from database.models_extended import RadarItem, RadarSaved, RadarSource, RadarUser
from web.api import radar as radar_api


def _request(user):
    return SimpleNamespace(state=SimpleNamespace(user=user))


def _user(uid=1, quota=1000, used=0):
    return SimpleNamespace(id=uid, role="radar", quota_bytes=quota, used_bytes=used)


class _FakeSession:
    """Очереди: scalar_one_or_none / .all() / session.get."""

    def __init__(self, scalar_results=(), all_results=(), scalars_seq=(), get_results=()):
        self._scalar = list(scalar_results)
        self._all = list(all_results)
        self._scalar_value = list(scalars_seq)  # для .scalar()
        self._get = list(get_results)
        self.added = []
        self.deleted = []
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, _stmt):
        result = MagicMock()
        result.scalar_one_or_none.return_value = self._scalar.pop(0) if self._scalar else None
        rows = self._all.pop(0) if self._all else []
        result.scalars.return_value.all.return_value = rows
        result.scalar.return_value = self._scalar_value.pop(0) if self._scalar_value else 0
        return result

    async def get(self, _model, _pk):
        return self._get.pop(0) if self._get else None

    def add(self, obj):
        self.added.append(obj)

    async def delete(self, obj):
        self.deleted.append(obj)

    async def flush(self):
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = 100

    async def commit(self):
        self.committed = True

    async def refresh(self, obj):
        pass


@pytest.mark.asyncio
async def test_save_item_not_in_own_feed_404():
    fake = _FakeSession(scalar_results=[None])
    with patch.object(radar_api, "AsyncSessionLocal", lambda: fake):
        with pytest.raises(HTTPException) as exc:
            await radar_api.save_item(radar_api.SaveIn(item_id=5), _request(_user()))
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_save_item_snapshots_and_accounts_quota():
    item = RadarItem(
        source_id=3,
        external_id="x",
        url="https://e.com/1",
        text="hello",
        media=[{"type": "photo", "url": "u"}],
        published_at=datetime(2026, 6, 12),
    )
    item.id = 42
    source = RadarSource(type="vk", key="-1", title="Гоньба")
    source.id = 3
    db_user = RadarUser(login="u", password_hash="h", quota_bytes=1000, used_bytes=10)
    db_user.id = 1

    fake = _FakeSession(scalar_results=[item, None], get_results=[source, db_user])

    async def fake_download(media, user_id, saved_id, *, quota_left):
        assert quota_left == 990
        return ([{"type": "photo", "url": "u", "file": "00.jpg", "bytes": 7}], 7)

    with (
        patch.object(radar_api, "AsyncSessionLocal", lambda: fake),
        patch("modules.radar.archive.download_media", side_effect=fake_download),
    ):
        result = await radar_api.save_item(radar_api.SaveIn(item_id=42), _request(_user()))

    assert result["created"] is True
    saved = next(o for o in fake.added if isinstance(o, RadarSaved))
    assert saved.source_title == "Гоньба"
    assert saved.text == "hello"
    assert saved.archived_bytes == 7
    assert db_user.used_bytes == 17
    assert fake.committed


@pytest.mark.asyncio
async def test_save_item_idempotent():
    item = RadarItem(source_id=3, external_id="x")
    item.id = 42
    existing = RadarSaved(user_id=1, item_id=42)
    existing.id = 7
    fake = _FakeSession(scalar_results=[item, existing])
    with patch.object(radar_api, "AsyncSessionLocal", lambda: fake):
        result = await radar_api.save_item(radar_api.SaveIn(item_id=42), _request(_user()))
    assert result["created"] is False
    assert fake.added == []


@pytest.mark.asyncio
async def test_delete_saved_returns_bytes_and_removes_dir():
    saved = RadarSaved(user_id=1, item_id=2, archived_bytes=30)
    saved.id = 9
    db_user = RadarUser(login="u", password_hash="h", quota_bytes=1000, used_bytes=100)
    fake = _FakeSession(scalar_results=[saved], get_results=[db_user])
    with (
        patch.object(radar_api, "AsyncSessionLocal", lambda: fake),
        patch("modules.radar.archive.remove_saved_dir") as rm,
    ):
        result = await radar_api.delete_saved(9, _request(_user(uid=1)))
    assert result == {"deleted": True}
    assert db_user.used_bytes == 70
    assert fake.deleted == [saved]
    rm.assert_called_once_with(1, 9)


@pytest.mark.asyncio
async def test_mark_seen_moves_cursor_forward_only():
    db_user = RadarUser(login="u", password_hash="h")
    db_user.last_seen_item_id = 50
    fake = _FakeSession(get_results=[db_user])
    with patch.object(radar_api, "AsyncSessionLocal", lambda: fake):
        result = await radar_api.mark_seen(radar_api.SeenIn(item_id=40), _request(_user()))
    assert result == {"last_seen_item_id": 50}  # назад не двигается
    assert not fake.committed

    fake2 = _FakeSession(get_results=[db_user])
    with patch.object(radar_api, "AsyncSessionLocal", lambda: fake2):
        result = await radar_api.mark_seen(radar_api.SeenIn(item_id=60), _request(_user()))
    assert result == {"last_seen_item_id": 60}
    assert fake2.committed


@pytest.mark.asyncio
async def test_get_saved_media_owner_only():
    fake = _FakeSession(scalar_results=[None])
    with patch.object(radar_api, "AsyncSessionLocal", lambda: fake):
        with pytest.raises(HTTPException) as exc:
            await radar_api.get_saved_media(5, "00.jpg", _request(_user()))
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_list_saved_reports_quota():
    # execute'ы: выборка сохранёнок, SUM(archived_bytes юзера), SUM(used_bytes всех).
    fake = _FakeSession(all_results=[[]], scalars_seq=[0, 123, 0])
    with patch.object(radar_api, "AsyncSessionLocal", lambda: fake):
        result = await radar_api.list_saved(_request(_user(quota=500)), before_id=None, limit=30)
    assert result["used_bytes"] == 123
    assert result["quota_bytes"] == 500
    assert result["items"] == []


@pytest.mark.asyncio
async def test_save_item_respects_global_archive_cap(monkeypatch):
    """Box-level enforcement (Ф1): суммарный архив у потолка → quota_left урезан."""
    monkeypatch.setenv("RADAR_ARCHIVE_MAX_BYTES", "1000")
    item = RadarItem(
        source_id=3,
        external_id="x",
        url="https://e.com/1",
        text="hi",
        media=[{"type": "photo", "url": "u"}],
        published_at=datetime(2026, 6, 12),
    )
    item.id = 42
    source = RadarSource(type="vk", key="-1", title="G")
    source.id = 3
    db_user = RadarUser(login="u", password_hash="h", quota_bytes=10**9, used_bytes=0)
    db_user.id = 1
    # 3 execute() до download_media: item, existing, SUM(used_bytes всех) → 950.
    fake = _FakeSession(
        scalar_results=[item, None], get_results=[source, db_user], scalars_seq=[0, 0, 950]
    )
    captured = {}

    async def fake_download(media, user_id, saved_id, *, quota_left):
        captured["quota_left"] = quota_left
        return ([{"type": "photo", "url": "u"}], 0)

    with (
        patch.object(radar_api, "AsyncSessionLocal", lambda: fake),
        patch("modules.radar.archive.download_media", side_effect=fake_download),
    ):
        await radar_api.save_item(radar_api.SaveIn(item_id=42), _request(_user()))
    # per_user_left огромный, global_left = 1000 - 950 = 50 → min = 50.
    assert captured["quota_left"] == 50


@pytest.mark.asyncio
async def test_list_saved_reports_archive_status(monkeypatch):
    """list_saved отдаёт box-level статус; global_used ≥ потолка → writable False."""
    monkeypatch.setenv("RADAR_ARCHIVE_MAX_BYTES", "1000")
    monkeypatch.setenv("RADAR_ARCHIVE_MIN_FREE_BYTES", "500")
    from modules.radar import archive as arch

    monkeypatch.setattr(arch, "disk_free_bytes", lambda: 10**9)
    # execute'ы: saved(.all), used(.scalar)=10, global(.scalar)=1200.
    fake = _FakeSession(all_results=[[]], scalars_seq=[0, 10, 1200])
    with patch.object(radar_api, "AsyncSessionLocal", lambda: fake):
        result = await radar_api.list_saved(_request(_user(quota=500)), before_id=None, limit=30)
    a = result["archive"]
    assert a["global_used_bytes"] == 1200
    assert a["max_bytes"] == 1000
    assert a["writable"] is False
