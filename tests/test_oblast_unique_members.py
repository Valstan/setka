"""Tests for weekly oblast unique-member (deduplicated) snapshots.

Pure region→oblast grouping is tested directly; the orchestration uses an
injected fake session + fetch callable (no DB / no VK), mirroring
test_members_snapshot.
"""

from __future__ import annotations

import asyncio
from datetime import date
from unittest.mock import AsyncMock, MagicMock

from modules import oblast_unique_members as om

DAY = date(2026, 6, 7)


# ----------------------------------------------------------------- group_regions_by_oblast


def test_group_regions_by_oblast_basic():
    regions = [
        (10, "oblast", None, -100),
        (1, "raion", 10, -1),
        (2, "raion", 10, -2),
        (20, "oblast", None, -200),
        (3, "raion", 20, -3),
        (99, "raion", None, -9),  # район без родителя-области → выпадает
    ]
    g = om.group_regions_by_oblast(regions)
    assert set(g.keys()) == {10, 20}
    assert set(g[10]) == {(10, -100), (1, -1), (2, -2)}
    assert set(g[20]) == {(20, -200), (3, -3)}


def test_group_skips_regions_without_group():
    # У области нет своей группы, но у района есть → группа существует с районом.
    regions = [(10, "oblast", None, None), (1, "raion", 10, -1), (2, "raion", 10, None)]
    assert om.group_regions_by_oblast(regions) == {10: [(1, -1)]}


def test_group_empty_oblast_dropped():
    regions = [(10, "oblast", None, None), (1, "raion", 10, None)]
    assert om.group_regions_by_oblast(regions) == {}


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


def test_collect_unions_member_ids_and_upserts():
    regions = [(10, "oblast", None, -100), (1, "raion", 10, -1), (2, "raion", 10, -2)]
    session = _FakeSession(regions)
    members = {100: ([1, 2, 3], True), 1: ([3, 4], True), 2: ([5], True)}

    async def fake_fetch(gid):
        return members[abs(int(gid))]

    out = asyncio.run(
        om.collect_oblast_unique_snapshots(
            snapshot_day=DAY,
            session_factory=lambda: _CM(session),
            fetch_member_ids=fake_fetch,
        )
    )
    assert out["success"] is True
    assert out["oblasts"] == 1
    assert out["written"] == 1
    d = out["details"][0]
    assert d["oblast_region_id"] == 10
    assert d["unique"] == 5  # {1,2,3,4,5}
    assert d["total"] == 6  # 3 + 2 + 1 (с дублями)
    assert d["groups"] == 3
    session.commit.assert_awaited_once()


def test_collect_closed_group_not_counted():
    regions = [(10, "oblast", None, -100), (1, "raion", 10, -1)]
    session = _FakeSession(regions)
    members = {100: ([1, 2], True), 1: ([], False)}  # район закрыт → пропущен

    async def fake_fetch(gid):
        return members[abs(int(gid))]

    out = asyncio.run(
        om.collect_oblast_unique_snapshots(
            snapshot_day=DAY,
            session_factory=lambda: _CM(session),
            fetch_member_ids=fake_fetch,
        )
    )
    d = out["details"][0]
    assert d["unique"] == 2
    assert d["groups"] == 1  # закрытая группа не вошла в group_count


def test_collect_no_oblasts_noop():
    regions = [(1, "raion", None, -1)]  # нет ни одной области
    session = _FakeSession(regions)
    out = asyncio.run(
        om.collect_oblast_unique_snapshots(
            snapshot_day=DAY,
            session_factory=lambda: _CM(session),
            fetch_member_ids=AsyncMock(return_value=([], True)),
        )
    )
    assert out["oblasts"] == 0
    assert out["written"] == 0
    session.commit.assert_not_awaited()


def test_collect_without_token_short_circuits(monkeypatch):
    monkeypatch.setattr(om, "_resolve_parse_token", AsyncMock(return_value=None))
    out = asyncio.run(
        om.collect_oblast_unique_snapshots(session_factory=lambda: _CM(_FakeSession([])))
    )
    assert out["success"] is False
    assert "token" in out["error"].lower()


# ----------------------------------------------------------------- celery registration


def test_task_registered_and_scheduled():
    from tasks.celery_app import app

    assert "tasks.celery_app.collect_oblast_unique_snapshots" in app.tasks
    assert "collect-oblast-unique-snapshots-weekly" in app.conf.beat_schedule
    entry = app.conf.beat_schedule["collect-oblast-unique-snapshots-weekly"]
    assert entry["task"] == "tasks.celery_app.collect_oblast_unique_snapshots"
