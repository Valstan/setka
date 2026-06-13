"""Тесты сбора метрик рекламных публикаций (С3, run_collect_stats).

VK-фетчер инжектируется (fetch_stats), сессия БД — фейковая (async CM).
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

from database.models import AdPublication
from modules.ad_cabinet import publication_stats as ps


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
        status="published",
    )
    defaults.update(kw)
    return AdPublication(**defaults)


def _run(rows, fetch_stats, only_client_id=None, now=datetime(2026, 6, 2, 12, 0)):
    session = _FakeSession(rows)
    out = asyncio.run(
        ps.run_collect_stats(
            session_factory=lambda: _FakeSessionCM(session),
            fetch_stats=fetch_stats,
            only_client_id=only_client_id,
            now=now,
        )
    )
    return out, session


def test_stats_collected_and_written():
    pub = _pub()
    fetch = lambda refs: {(-100, 55): {"views": 1000, "likes": 20, "reposts": 3}}  # noqa: E731
    out, session = _run([pub], fetch_stats=fetch)
    assert out == {"updated": 1, "checked": 1}
    assert pub.views == 1000
    assert pub.likes == 20
    assert pub.reposts == 3
    assert pub.stats_updated_at == datetime(2026, 6, 2, 12, 0)
    session.commit.assert_awaited()


def test_missing_stats_left_untouched():
    """Пост, по которому VK не вернул данные, не обновляется."""
    pub = _pub()
    out, _ = _run([pub], fetch_stats=lambda refs: {})
    assert out == {"updated": 0, "checked": 1}
    assert pub.views is None
    assert pub.stats_updated_at is None


def test_empty_rows_noop():
    out, session = _run([], fetch_stats=lambda refs: {(-100, 55): {"views": 1}})
    assert out == {"updated": 0, "checked": 0}
    session.commit.assert_not_awaited()


def test_fetch_exception_is_safe():
    def boom(refs):
        raise RuntimeError("vk down")

    pub = _pub()
    out, _ = _run([pub], fetch_stats=boom)
    assert out == {"updated": 0, "checked": 1}
    assert pub.views is None


def test_partial_update_multiple_pubs():
    p1 = _pub(id=1, vk_post_id=11)
    p2 = _pub(id=2, vk_post_id=22)
    fetch = lambda refs: {(-100, 11): {"views": 5, "likes": 1, "reposts": 0}}  # noqa: E731
    out, _ = _run([p1, p2], fetch_stats=fetch)
    assert out == {"updated": 1, "checked": 2}
    assert p1.views == 5
    assert p2.views is None
