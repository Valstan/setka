"""Фото кабинета вне БД: ретенция, каталог по env, пол свободного места (PR 1.8).

- архивный клиент → каталог удалён целиком; неизвестный каталог (клиента нет) — тоже;
- живой клиент: старые файлы без ссылок из активных постов удаляются, свежие и
  сослатые остаются; чужие имена каталогов пропускаются;
- ``AD_UPLOAD_DIR`` переключает корень, пусто — старый путь в дереве репо;
- загрузка при нехватке места отвечает 507, а не пишет в полный диск.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from database.models import AdClient, AdScheduledPost
from modules.ad_cabinet import photo_retention

NOW = datetime(2026, 9, 5, 4, 20, 0)


def _touch(path: Path, *, age_days: int, size: int = 10) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    ts = (NOW - timedelta(days=age_days)).timestamp()
    os.utime(path, (ts, ts))
    return path


@pytest.mark.asyncio
async def test_retention_rules(db_session, tmp_path):
    live = AdClient(name="Живой")
    gone = AdClient(name="Архив", is_archived=True)
    db_session.add_all([live, gone])
    await db_session.flush()
    db_session.add(
        AdScheduledPost(
            community_vk_id=-1,
            text="t",
            publish_date=NOW + timedelta(days=1),
            status="scheduled",
            client_id=live.id,
            image_names=["used.jpg"],
            price=Decimal("0"),
        )
    )
    await db_session.commit()

    root = tmp_path / "ad_uploads"
    _touch(root / str(live.id) / "used.jpg", age_days=400)  # старое, но в активном посте
    _touch(root / str(live.id) / "old.jpg", age_days=200, size=100)  # старое, без ссылок
    _touch(root / str(live.id) / "fresh.jpg", age_days=3)  # свежее
    _touch(root / str(gone.id) / "a.jpg", age_days=1, size=50)
    _touch(root / "999" / "orphan.jpg", age_days=1, size=7)  # клиента нет
    (root / "not-a-client").mkdir()

    class _Factory:
        def __call__(self):
            return self

        async def __aenter__(self):
            return db_session

        async def __aexit__(self, *a):
            return False

    stats = await photo_retention.run_photo_retention(
        session_factory=_Factory(), root=root, now=NOW, keep_days=180
    )
    assert stats["archived_removed"] == 2 and stats["files_removed"] == 1
    assert stats["bytes_freed"] == 100 + 50 + 7
    assert (root / str(live.id) / "used.jpg").exists()
    assert (root / str(live.id) / "fresh.jpg").exists()
    assert not (root / str(live.id) / "old.jpg").exists()
    assert not (root / str(gone.id)).exists() and not (root / "999").exists()
    assert (root / "not-a-client").exists()

    again = await photo_retention.run_photo_retention(
        session_factory=_Factory(), root=root, now=NOW, keep_days=180
    )
    assert again["files_removed"] == 0 and again["archived_removed"] == 0


@pytest.mark.asyncio
async def test_missing_root_is_noop(tmp_path):
    stats = await photo_retention.run_photo_retention(
        session_factory=lambda: None, root=tmp_path / "nope", now=NOW
    )
    assert stats["dirs"] == 0


def test_upload_root_follows_env(monkeypatch, tmp_path):
    import config.runtime as runtime
    from web.api import advertiser_cabinet as ac

    monkeypatch.setenv("AD_UPLOAD_DIR", str(tmp_path / "store"))
    assert ac._upload_root() == tmp_path / "store"
    d = ac._client_photo_dir(7)
    assert d == tmp_path / "store" / "7" and d.is_dir()

    monkeypatch.setenv("AD_UPLOAD_DIR", "")
    assert runtime.ad_upload_dir() is None
    assert ac._upload_root().parts[-2:] == ("uploads", "advertiser")


def test_free_space_floor(monkeypatch, tmp_path):
    from web.api import advertiser_cabinet as ac

    monkeypatch.setenv("AD_UPLOAD_DIR", str(tmp_path))
    monkeypatch.setenv("AD_UPLOAD_MIN_FREE_BYTES", "1000")

    class _Usage:
        def __init__(self, free):
            self.free = free

    monkeypatch.setattr(ac.shutil, "disk_usage", lambda p: _Usage(free=1500))
    assert ac._fits_disk(400) is True
    assert ac._fits_disk(600) is False  # 1500 - 600 < 1000


def test_retention_task_registered():
    from tasks.celery_app import app

    assert "tasks.celery_app.ad_photo_retention" in app.tasks
    assert "ad-photo-retention" in app.conf.beat_schedule
