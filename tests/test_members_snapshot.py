"""Tests for daily region main-INFO-group member-count snapshots (growth chart).

Pure row-building is tested directly; the orchestration uses an injected fake
session + fetch callable (no DB / no VK), mirroring test_publish_reconciler.
"""

from __future__ import annotations

import asyncio
from datetime import date
from unittest.mock import AsyncMock, MagicMock

from modules import members_snapshot as ms

DAY = date(2026, 6, 6)


# ----------------------------------------------------------------- build_snapshot_rows


def test_build_rows_matches_on_abs_sign():
    """regions.vk_group_id is negative for groups; VK returns positive id."""
    regions = [(1, -100), (2, 200)]
    vk_info = [{"id": 100, "members_count": 1500}, {"id": 200, "members_count": 42}]
    rows, missing = ms.build_snapshot_rows(regions, vk_info, DAY)
    assert missing == []
    assert {r["region_id"]: r["members_count"] for r in rows} == {1: 1500, 2: 42}
    assert all(r["snapshot_date"] == DAY for r in rows)


def test_build_rows_marks_missing_for_absent_or_banned():
    """No VK item / deactivated / no members_count / null vk_group_id → missing, not a row."""
    regions = [(1, -100), (2, -200), (3, -300), (4, None)]
    vk_info = [
        {"id": 100, "members_count": 10},  # ok
        {"id": 200, "deactivated": "banned", "members_count": 5},  # banned → skip
        {"id": 300},  # no members_count → skip
    ]
    rows, missing = ms.build_snapshot_rows(regions, vk_info, DAY)
    assert [r["region_id"] for r in rows] == [1]
    assert sorted(missing) == [2, 3, 4]


def test_build_rows_handles_empty_vk_info():
    rows, missing = ms.build_snapshot_rows([(1, -100)], None, DAY)
    assert rows == []
    assert missing == [1]


# ----------------------------------------------------------------- orchestration


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows
        self.commit = AsyncMock()
        self.executed = []

    async def execute(self, stmt):
        self.executed.append(stmt)
        r = MagicMock()
        r.all.return_value = self._rows
        return r


class _CM:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        return False


def test_collect_writes_snapshots_and_counts_missing():
    regions = [(1, -100), (2, 200), (3, -300)]
    session = _FakeSession(regions)
    vk_info = [{"id": 100, "members_count": 1500}, {"id": 200, "members_count": 42}]

    out = asyncio.run(
        ms.collect_member_snapshots(
            snapshot_day=DAY,
            session_factory=lambda: _CM(session),
            fetch_members=AsyncMock(return_value=vk_info),
        )
    )

    assert out["success"] is True
    assert out["regions"] == 3
    assert out["written"] == 2
    assert out["missing"] == 1
    assert out["snapshot_date"] == "2026-06-06"
    session.commit.assert_awaited_once()  # upsert committed exactly once


def test_collect_skips_commit_when_nothing_resolved():
    """All region groups banned → no rows → no insert/commit, but still success."""
    regions = [(1, -100), (2, -200)]
    session = _FakeSession(regions)

    out = asyncio.run(
        ms.collect_member_snapshots(
            snapshot_day=DAY,
            session_factory=lambda: _CM(session),
            fetch_members=AsyncMock(return_value=[]),
        )
    )

    assert out["success"] is True
    assert out["written"] == 0
    assert out["missing"] == 2
    session.commit.assert_not_awaited()


def test_collect_empty_pool_is_noop():
    session = _FakeSession([])
    out = asyncio.run(
        ms.collect_member_snapshots(
            snapshot_day=DAY,
            session_factory=lambda: _CM(session),
            fetch_members=AsyncMock(return_value=[]),
        )
    )
    assert out == {
        "success": True,
        "regions": 0,
        "written": 0,
        "missing": 0,
        "snapshot_date": "2026-06-06",
    }


def test_collect_without_token_short_circuits(monkeypatch):
    """No injected fetch and no active token → graceful skip, no DB touch."""
    monkeypatch.setattr(ms, "_resolve_parse_token", AsyncMock(return_value=None))
    out = asyncio.run(ms.collect_member_snapshots(session_factory=lambda: _CM(_FakeSession([]))))
    assert out["success"] is False
    assert "token" in out["error"].lower()


# ----------------------------------------------------------------- celery registration


def test_task_registered_and_scheduled():
    from tasks.celery_app import app

    assert "tasks.celery_app.collect_member_snapshots" in app.tasks
    assert "collect-member-snapshots-daily" in app.conf.beat_schedule
    entry = app.conf.beat_schedule["collect-member-snapshots-daily"]
    assert entry["task"] == "tasks.celery_app.collect_member_snapshots"
