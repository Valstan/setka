"""Сканер предложки помечает заявки, чьих постов в VK больше нет (аудит 2026-09-05).

Случай Анны Валиевой 05.09: два поста от 05.08 давно исчезли из предложки, а
заявки висели «new» и ловили [15] при планировании. Правила: помечаем только
когда VK ответил (не упал) и выдача полная (<100).
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from database.models import AdRequest
from modules.ad_cabinet import scanner

REGION = {"region_id": 1, "region_name": "Малмыж", "region_code": "mi", "vk_group_id": -158787639}


class _Checker:
    def __init__(self, posts, *, error=None):
        self._posts = posts
        self.last_fetch_error = error

    def fetch_suggested_posts(self, group_id):
        return list(self._posts)


async def _classify_none(post):
    return False, 0, []


def _req(vk_post_id, status="new", **kw):
    d = dict(
        id=int(vk_post_id),  # BigInteger PK без sqlite-варианта — id задаём сами
        origin="suggested",
        community_vk_id=-158787639,
        vk_post_id=vk_post_id,
        author_vk_id=1,
        status=status,
        text_snapshot="t",
    )
    d.update(kw)
    return AdRequest(**d)


@pytest.mark.asyncio
async def test_missing_posts_marked_vanished(db_session):
    db_session.add_all([_req(77681), _req(77683), _req(78276), _req(500, status="contacted")])
    await db_session.commit()
    stats = await scanner.scan_region_group(
        db_session, _Checker([{"vk_post_id": 78276}]), REGION, classify_fn=_classify_none
    )
    assert stats["vanished"] == 2
    rows = {r.vk_post_id: r.status for r in (await db_session.execute(select(AdRequest))).scalars()}
    assert rows == {77681: "vanished", 77683: "vanished", 78276: "new", 500: "contacted"}


@pytest.mark.asyncio
async def test_no_marking_when_vk_failed_or_page_full(db_session):
    db_session.add(_req(77681))
    await db_session.commit()
    stats = await scanner.scan_region_group(
        db_session, _Checker([], error="[6] Too many requests"), REGION, classify_fn=_classify_none
    )
    assert stats["vanished"] == 0
    full = [{"vk_post_id": 10_000 + i} for i in range(100)]
    stats = await scanner.scan_region_group(
        db_session, _Checker(full), REGION, classify_fn=_classify_none
    )
    assert stats["vanished"] == 0
    row = (await db_session.execute(select(AdRequest))).scalar_one()
    assert row.status == "new"
