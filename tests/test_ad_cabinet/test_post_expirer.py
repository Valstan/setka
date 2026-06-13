"""Тесты авто-снятия рекламных постов по сроку (С2, run_expiry).

VK-удаление инжектируется (delete_post), сессия БД — фейковая (async CM),
чтобы покрыть чистую логику снятия без сети и реальной БД.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

from database.models import AdPublication
from modules.ad_cabinet import post_expirer as pe


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows
        self.add = MagicMock()
        self.commit = AsyncMock()

    async def execute(self, stmt):
        r = MagicMock()
        r.scalars.return_value.all.return_value = self._rows
        return r


class _FakeSessionCM:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        return False


def _pub(**kw):
    defaults = dict(
        id=1,
        client_id=3,
        community_vk_id=-100,
        vk_post_id=55,
        scheduled_post_id=9,
        status="published",
        expires_at=datetime(2026, 6, 1, 10, 0),
    )
    defaults.update(kw)
    return AdPublication(**defaults)


def _run(rows, delete_post, now=datetime(2026, 6, 2, 12, 0)):
    session = _FakeSession(rows)
    out = asyncio.run(
        pe.run_expiry(
            session_factory=lambda: _FakeSessionCM(session),
            delete_post=delete_post,
            now=now,
        )
    )
    return out, session


def test_expired_post_is_removed():
    pub = _pub()
    out, session = _run([pub], delete_post=lambda owner, pid: True)
    assert out["removed"] == 1
    assert pub.status == "removed"
    assert pub.removed_at is not None
    # событие 'removed' добавлено в таймлайн
    assert session.add.call_count >= 1
    session.commit.assert_awaited()


def test_delete_failure_keeps_published():
    """wall.delete не удался → пост остаётся published (повторим завтра)."""
    pub = _pub()
    out, session = _run([pub], delete_post=lambda owner, pid: False)
    assert out["removed"] == 0
    assert pub.status == "published"
    assert pub.removed_at is None
    session.add.assert_not_called()


def test_no_vk_post_id_skipped():
    pub = _pub(vk_post_id=None)
    out, session = _run([pub], delete_post=lambda owner, pid: True)
    assert out["removed"] == 0
    assert pub.status == "published"
    session.add.assert_not_called()


def test_deleter_exception_is_safe():
    def boom(owner, pid):
        raise RuntimeError("vk down")

    pub = _pub()
    out, _ = _run([pub], delete_post=boom)
    assert out["removed"] == 0
    assert pub.status == "published"


def test_empty_selection_noop():
    """Нет вышедших постов с истёкшим сроком → ничего не делаем."""
    out, session = _run([], delete_post=lambda owner, pid: True)
    assert out == {"removed": 0, "checked": 0}
    session.add.assert_not_called()


def test_multiple_posts_mixed_results():
    p1 = _pub(id=1, vk_post_id=11)
    p2 = _pub(id=2, vk_post_id=22)
    # первый удаляется, второй — нет.
    out, _ = _run([p1, p2], delete_post=lambda owner, pid: pid == 11)
    assert out == {"removed": 1, "checked": 2}
    assert p1.status == "removed"
    assert p2.status == "published"
