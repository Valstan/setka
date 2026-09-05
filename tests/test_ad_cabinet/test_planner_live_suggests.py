"""Планировщик показывает ВСЮ живую предложку (инцидент 2026-09-05).

- посты ниже порога классификатора заводятся как new с пометкой, can_message пуст;
- уже известные заявки не дублируются; vanished при повторном появлении → new;
- skipped/published — решения оператора, не трогаем;
- VK не ответил → error, база не меняется; нет токена → error.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import select

from database.models import AdRequest, Region
from modules.ad_cabinet import suggested_planner as sp

GID = -158787639


class _Checker:
    def __init__(self, posts=None, error=None, raise_exc=False):
        self.posts = posts or []
        self.error = error
        self.raise_exc = raise_exc
        self.last_fetch_error = None
        self.calls = []

    def fetch_suggested_posts(self, group_id):
        self.calls.append(group_id)
        if self.raise_exc:
            raise RuntimeError("boom")
        self.last_fetch_error = self.error
        return [] if self.error else list(self.posts)


def _post(pid, text="Устали от платежей по кредитам? Банкротство. +7 922 000-00-00"):
    return {
        "community_vk_id": GID,
        "vk_post_id": pid,
        "author_vk_id": 139799739,
        "author_name": "Анна Валиева",
        "text": text,
        "attachments": [],
        "photo_urls": [],
    }


async def _not_ad(post):
    return False, 2, ["контакт: номер телефона"]


async def _sqlite_insert(session, region, parsed, score, reasons):
    """Аналог scanner._insert_if_new без Postgres-диалекта."""
    exists = (
        await session.execute(
            select(AdRequest.id).where(
                AdRequest.community_vk_id == parsed["community_vk_id"],
                AdRequest.vk_post_id == parsed["vk_post_id"],
            )
        )
    ).first()
    if exists:
        return False
    session.add(
        AdRequest(
            id=abs(int(parsed["vk_post_id"])),
            region_id=region["region_id"],
            community_vk_id=parsed["community_vk_id"],
            community_name=region["region_name"],
            vk_post_id=parsed["vk_post_id"],
            author_vk_id=parsed.get("author_vk_id"),
            author_name=parsed.get("author_name"),
            text_snapshot=parsed.get("text", ""),
            score=score,
            reasons_json=reasons,
            status="new",
            origin="suggested",
            detected_at=datetime(2026, 9, 5, 12, 0),
        )
    )
    await session.flush()
    return True


async def _region(session):
    r = Region(name="Малмыж", code="mi", vk_group_id=GID, is_active=True)
    session.add(r)
    await session.flush()
    return r


@pytest.mark.asyncio
async def test_non_ad_posts_are_inserted_for_the_planner(db_session):
    r = await _region(db_session)
    checker = _Checker([_post(78278), _post(78274)])
    out = await sp.sync_live_suggests(
        db_session, r, checker=checker, classify_fn=_not_ad, insert_fn=_sqlite_insert
    )
    assert out == {"fetched": 2, "inserted": 2, "revived": 0, "error": None}
    assert checker.calls == [GID]
    rows = (
        (await db_session.execute(select(AdRequest).order_by(AdRequest.vk_post_id))).scalars().all()
    )
    assert [x.vk_post_id for x in rows] == [78274, 78278]
    assert all(x.status == "new" and x.score == 2 and x.can_message is None for x in rows)
    assert "из планировщика" in " ".join(rows[0].reasons_json)

    again = await sp.sync_live_suggests(
        db_session, r, checker=checker, classify_fn=_not_ad, insert_fn=_sqlite_insert
    )
    assert again["inserted"] == 0 and again["fetched"] == 2


@pytest.mark.asyncio
async def test_vanished_revived_but_operator_decisions_kept(db_session):
    r = await _region(db_session)
    db_session.add_all(
        [
            AdRequest(
                id=1, community_vk_id=GID, vk_post_id=100, status="vanished", origin="suggested"
            ),
            AdRequest(
                id=2, community_vk_id=GID, vk_post_id=200, status="skipped", origin="suggested"
            ),
            AdRequest(
                id=3, community_vk_id=GID, vk_post_id=300, status="published", origin="suggested"
            ),
            AdRequest(
                id=4, community_vk_id=GID, vk_post_id=400, status="vanished", origin="suggested"
            ),
        ]
    )
    await db_session.flush()
    checker = _Checker([_post(100), _post(200), _post(300)])
    out = await sp.sync_live_suggests(
        db_session, r, checker=checker, classify_fn=_not_ad, insert_fn=_sqlite_insert
    )
    assert out["inserted"] == 0 and out["revived"] == 1
    by_id = {
        x.vk_post_id: x.status for x in (await db_session.execute(select(AdRequest))).scalars()
    }
    assert by_id == {100: "new", 200: "skipped", 300: "published", 400: "vanished"}


@pytest.mark.asyncio
async def test_vk_failure_or_no_token_changes_nothing(db_session):
    r = await _region(db_session)
    out = await sp.sync_live_suggests(
        db_session,
        r,
        checker=_Checker(error="[6] Too many requests"),
        classify_fn=_not_ad,
        insert_fn=_sqlite_insert,
    )
    assert out["error"] and out["fetched"] == 0
    out = await sp.sync_live_suggests(
        db_session,
        r,
        checker=_Checker(raise_exc=True),
        classify_fn=_not_ad,
        insert_fn=_sqlite_insert,
    )
    assert "boom" in out["error"]
    out = await sp.sync_live_suggests(db_session, r, checker=None)
    assert out["error"]
    assert (await db_session.execute(select(AdRequest))).scalars().all() == []
