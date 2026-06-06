"""Daily snapshots of VK community subscriber counts.

Foundation for the subscriber-growth chart (owner-request 2026-06-05).
`communities` carries neither `members_count` nor any history, so there is
nothing to plot until data accumulates. This module's daily beat task
fetches `groups.getById(fields=members_count)` in batches for every active
community and records one immutable row per (community, day) into
`community_member_snapshots`.

Re-running for the same day is idempotent: the upsert targets the unique
(community_id, snapshot_date) index and overwrites the count.

The pure mapping (`build_snapshot_rows`) is split out so it can be unit-tested
without a DB or VK — the orchestration (`collect_member_snapshots`) wires
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
from database.models import Community, CommunityMemberSnapshot

logger = logging.getLogger(__name__)


def _pick_parse_token() -> Optional[str]:
    """First VK user-token valid for READ right now (respects cooldown)."""
    from modules.vk_token_router import get_active_parse_tokens_sync

    for name, tok in get_active_parse_tokens_sync().items():
        if tok:
            logger.debug("member-snapshots: using token %s", name)
            return tok
    return None


def build_snapshot_rows(
    communities: Iterable[Tuple[int, Optional[int]]],
    vk_info: Optional[List[Dict[str, Any]]],
    snapshot_day: date,
) -> Tuple[List[Dict[str, Any]], List[int]]:
    """Map communities + ``groups.getById`` items → snapshot rows.

    ``communities`` — iterable of ``(community_id, vk_id)``. ``vk_id`` may be
    stored with either sign in the DB, so matching is done on ``abs``; VK
    returns a positive ``id``.

    Returns ``(rows, missing)``:
      * ``rows``    — ``[{community_id, members_count, snapshot_date}]`` ready to upsert;
      * ``missing`` — ``community_id`` with no usable count (banned / closed /
                      deleted group, or absent from the VK response).
    """
    by_gid: Dict[int, int] = {}
    for item in vk_info or []:
        if not isinstance(item, dict) or item.get("deactivated"):
            continue
        try:
            gid = abs(int(item.get("id")))
        except (TypeError, ValueError):
            continue
        mc = item.get("members_count")
        if mc is None:
            continue
        try:
            by_gid[gid] = int(mc)
        except (TypeError, ValueError):
            continue

    rows: List[Dict[str, Any]] = []
    missing: List[int] = []
    for community_id, vk_id in communities:
        try:
            gid = abs(int(vk_id))
        except (TypeError, ValueError):
            missing.append(community_id)
            continue
        if gid in by_gid:
            rows.append(
                {
                    "community_id": community_id,
                    "members_count": by_gid[gid],
                    "snapshot_date": snapshot_day,
                }
            )
        else:
            missing.append(community_id)
    return rows, missing


async def _default_fetch_members(token: str, gids: List[int]) -> List[Dict[str, Any]]:
    """Real VK fetch: build a client and batch ``groups.getById``.

    Sync + rate-limited under the hood, so it runs in a worker thread to keep
    the task's event loop free during the (batched) network calls.
    """
    from modules.vk_monitor.vk_client import VKClient

    client = VKClient(token=token)
    return await asyncio.to_thread(client.get_groups_by_ids, gids, "members_count")


async def collect_member_snapshots(
    *,
    token: Optional[str] = None,
    snapshot_day: Optional[date] = None,
    session_factory: Any = None,
    fetch_members: Any = None,
) -> Dict[str, Any]:
    """Fetch members_count for all active communities and upsert today's snapshot.

    Idempotent for the same day. Returns a summary dict
    ``{success, communities, written, missing, snapshot_date}``.

    ``session_factory`` and ``fetch_members`` are injectable for tests (default
    to the real async session and VK client). ``fetch_members`` is an async
    callable ``(gids: List[int]) -> List[dict]`` of ``groups.getById`` items.
    """
    snapshot_day = snapshot_day or date.today()
    session_factory = session_factory or AsyncSessionLocal

    if fetch_members is None:
        token = token or _pick_parse_token()
        if not token:
            logger.warning("member-snapshots: no active parse token — skipping")
            return {"success": False, "error": "no active parse token"}

        async def fetch_members(gids: List[int]) -> List[Dict[str, Any]]:
            return await _default_fetch_members(token, gids)

    async with session_factory() as session:
        result = await session.execute(
            select(Community.id, Community.vk_id).where(Community.is_active.is_(True))
        )
        communities: List[Tuple[int, Optional[int]]] = [(r[0], r[1]) for r in result.all()]

    if not communities:
        return {
            "success": True,
            "communities": 0,
            "written": 0,
            "missing": 0,
            "snapshot_date": snapshot_day.isoformat(),
        }

    gids = [abs(int(vk_id)) for _, vk_id in communities if vk_id is not None]
    vk_info = await fetch_members(gids)

    rows, missing = build_snapshot_rows(communities, vk_info, snapshot_day)

    written = 0
    if rows:
        async with session_factory() as session:
            stmt = pg_insert(CommunityMemberSnapshot).values(rows)
            stmt = stmt.on_conflict_do_update(
                index_elements=["community_id", "snapshot_date"],
                set_={"members_count": stmt.excluded.members_count},
            )
            await session.execute(stmt)
            await session.commit()
            written = len(rows)

    logger.info(
        "member-snapshots: %d active, %d written, %d missing (day=%s)",
        len(communities),
        written,
        len(missing),
        snapshot_day,
    )
    return {
        "success": True,
        "communities": len(communities),
        "written": written,
        "missing": len(missing),
        "snapshot_date": snapshot_day.isoformat(),
    }
