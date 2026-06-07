"""Weekly snapshot of UNIQUE (deduplicated) subscribers per oblast.

Owner-request 2026-06-07. The growth chart can sum the main INFO groups of an
oblast (районы + областная группа) into a "Σ область" line — but that sum is
inflated: a person subscribed to 3 district groups of the oblast is counted 3
times. To compare OBLASTS by their *clean* reach, we union the member-id sets of
all the oblast's main groups (the oblast itself + its raions,
``parent_region_id = oblast.id``) via ``groups.getMembers`` and count uniques.

Cheap by design: only the ~16 MAIN INFO groups are deduplicated (not the ~840
source-pool communities), ``groups.getMembers`` returns 1000 ids/call → tens of
calls total. Runs on a **weekly** night beat (slow-moving metric). One immutable
row per (oblast, day) into ``oblast_unique_member_snapshots`` (migration 034).

Closed groups / no-access (VK error 15) are skipped — the union counts available
groups and ``group_count`` records how many actually went in.

The pure mapping (`group_regions_by_oblast`) is split out for unit testing
without a DB or VK; the orchestration (`collect_oblast_unique_snapshots`) wires
tokens, session and the VK client around it.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from database.connection import AsyncSessionLocal
from database.models import OblastUniqueMemberSnapshot, Region
from modules.members_snapshot import _resolve_parse_token

logger = logging.getLogger(__name__)


def group_regions_by_oblast(
    regions: Iterable[Tuple[int, Optional[str], Optional[int], Optional[int]]],
) -> Dict[int, List[Tuple[int, int]]]:
    """Map regions → ``{oblast_region_id: [(region_id, vk_group_id), …]}``.

    ``regions`` — iterable of ``(region_id, kind, parent_region_id, vk_group_id)``.
    Each oblast (``kind == 'oblast'``) collects its OWN main group plus the main
    groups of its raions (``parent_region_id == oblast.id``). Regions without a
    ``vk_group_id`` are skipped (nothing to dedup); oblasts that end up with no
    groups are dropped. Returned membership preserves no order guarantee.
    """
    rows = list(regions)
    oblast_ids = {int(rid) for rid, kind, _parent, _gid in rows if kind == "oblast"}
    groups: Dict[int, List[Tuple[int, int]]] = {ob: [] for ob in oblast_ids}
    for rid, kind, parent, gid in rows:
        if gid is None:
            continue
        rid = int(rid)
        if kind == "oblast" and rid in groups:
            groups[rid].append((rid, int(gid)))
        elif parent is not None and int(parent) in groups:
            groups[int(parent)].append((rid, int(gid)))
    return {ob: members for ob, members in groups.items() if members}


def _make_fetcher(token: str):
    """Real VK member-id fetcher bound to a client (runs sync calls off-loop)."""
    from modules.vk_monitor.vk_client import VKClient

    client = VKClient(token=token)

    async def _fetch(group_id: int) -> Tuple[List[int], bool]:
        return await asyncio.to_thread(client.get_group_member_ids, group_id)

    return _fetch


async def collect_oblast_unique_snapshots(
    *,
    token: Optional[str] = None,
    snapshot_day: Optional[date] = None,
    session_factory: Any = None,
    fetch_member_ids: Any = None,
) -> Dict[str, Any]:
    """Union member-id sets of each oblast's main groups → upsert today's unique snapshot.

    Idempotent for the same day. Returns
    ``{success, oblasts, written, snapshot_date, details}``.

    ``session_factory`` and ``fetch_member_ids`` are injectable for tests.
    ``fetch_member_ids`` is an async callable ``(vk_group_id) -> (ids, complete)``.
    """
    snapshot_day = snapshot_day or date.today()
    session_factory = session_factory or AsyncSessionLocal

    if fetch_member_ids is None:
        token = token or await _resolve_parse_token()
        if not token:
            logger.warning("oblast-unique: no active parse token — skipping")
            return {"success": False, "error": "no active parse token"}
        fetch_member_ids = _make_fetcher(token)

    async with session_factory() as session:
        result = await session.execute(
            select(
                Region.id,
                Region.kind,
                Region.parent_region_id,
                Region.vk_group_id,
            ).where(Region.is_active.is_(True))
        )
        regions = [(r[0], r[1], r[2], r[3]) for r in result.all()]

    by_oblast = group_regions_by_oblast(regions)
    if not by_oblast:
        return {
            "success": True,
            "oblasts": 0,
            "written": 0,
            "snapshot_date": snapshot_day.isoformat(),
            "details": [],
        }

    rows: List[Dict[str, Any]] = []
    details: List[Dict[str, Any]] = []
    for oblast_id, members in by_oblast.items():
        union: set = set()
        total_with_dupes = 0
        group_count = 0
        for _rid, gid in members:
            ids, complete = await fetch_member_ids(gid)
            if complete or ids:
                group_count += 1
            total_with_dupes += len(ids)
            union.update(ids)
        rows.append(
            {
                "oblast_region_id": oblast_id,
                "unique_count": len(union),
                "total_with_dupes": total_with_dupes,
                "group_count": group_count,
                "snapshot_date": snapshot_day,
            }
        )
        details.append(
            {
                "oblast_region_id": oblast_id,
                "unique": len(union),
                "total": total_with_dupes,
                "groups": group_count,
            }
        )

    written = 0
    if rows:
        async with session_factory() as session:
            stmt = pg_insert(OblastUniqueMemberSnapshot).values(rows)
            stmt = stmt.on_conflict_do_update(
                index_elements=["oblast_region_id", "snapshot_date"],
                set_={
                    "unique_count": stmt.excluded.unique_count,
                    "total_with_dupes": stmt.excluded.total_with_dupes,
                    "group_count": stmt.excluded.group_count,
                },
            )
            await session.execute(stmt)
            await session.commit()
            written = len(rows)

    logger.info(
        "oblast-unique: %d oblasts, %d written (day=%s) %s",
        len(by_oblast),
        written,
        snapshot_day,
        details,
    )
    return {
        "success": True,
        "oblasts": len(by_oblast),
        "written": written,
        "snapshot_date": snapshot_day.isoformat(),
        "details": details,
    }
