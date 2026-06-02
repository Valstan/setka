"""Тесты scanner: вставка новых заявок, пропуск не-рекламы, дедуп."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from modules.ad_cabinet import scanner

_REGION = {
    "region_id": 7,
    "region_name": "Малмыж",
    "region_code": "mi",
    "vk_group_id": -100,
}


def _post(text="реклама"):
    return {
        "vk_post_id": 1,
        "community_vk_id": -100,
        "author_vk_id": 42,
        "signer_id": None,
        "peer_id": 42,
        "author_is_group": False,
        "author_name": "Иван",
        "text": text,
        "attachments": [],
        "photo_urls": [],
    }


async def _classify_ad(post, ctx=None):
    return True, 5, ["test"]


async def _classify_not(post, ctx=None):
    return False, 0, []


async def test_inserts_new_ad():
    checker = MagicMock()
    checker.fetch_suggested_posts.return_value = [_post()]
    session = AsyncMock()
    session.execute = AsyncMock(return_value=MagicMock(rowcount=1))

    stats = await scanner.scan_region_group(session, checker, _REGION, classify_fn=_classify_ad)
    assert stats["scanned"] == 1
    assert stats["ads"] == 1
    assert stats["new"] == 1
    session.commit.assert_awaited()


async def test_skips_non_ad():
    checker = MagicMock()
    checker.fetch_suggested_posts.return_value = [_post(text="обычная новость")]
    session = AsyncMock()
    session.execute = AsyncMock(return_value=MagicMock(rowcount=1))

    stats = await scanner.scan_region_group(session, checker, _REGION, classify_fn=_classify_not)
    assert stats["ads"] == 0
    assert stats["new"] == 0
    session.execute.assert_not_awaited()


async def test_dedup_existing_row():
    checker = MagicMock()
    checker.fetch_suggested_posts.return_value = [_post()]
    session = AsyncMock()
    # ON CONFLICT DO NOTHING для уже существующей заявки → 0 затронутых строк.
    session.execute = AsyncMock(return_value=MagicMock(rowcount=0))

    stats = await scanner.scan_region_group(session, checker, _REGION, classify_fn=_classify_ad)
    assert stats["ads"] == 1
    assert stats["new"] == 0
