"""Тесты dm_scanner (блок A): вставка ЛС-заявок, пропуск не-рекламы, дедуп."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from modules.ad_cabinet import dm_scanner

_REGION = {
    "region_id": 7,
    "region_name": "Малмыж",
    "region_code": "mi",
    "vk_group_id": -100,
}


def _dialog(text="размещу рекламу", author_is_group=False):
    return {
        "vk_post_id": None,
        "community_vk_id": -100,
        "author_vk_id": -200 if author_is_group else 42,
        "signer_id": None,
        "peer_id": 42,
        "author_is_group": author_is_group,
        "author_name": "Иван",
        "text": text,
        "attachments": [],
        "photo_urls": [],
        "last_message_id": 999,
    }


async def _classify_ad(post, ctx=None):
    return True, 5, ["test"]


async def _classify_not(post, ctx=None):
    return False, 0, []


async def test_inserts_new_dm_ad():
    checker = MagicMock()
    checker.fetch_inbound_dialogs.return_value = [_dialog()]
    session = AsyncMock()
    session.execute = AsyncMock(return_value=MagicMock(rowcount=1))

    stats = await dm_scanner.scan_region_dialogs(
        session, checker, _REGION, classify_fn=_classify_ad
    )
    assert stats["scanned"] == 1
    assert stats["ads"] == 1
    assert stats["new"] == 1
    session.commit.assert_awaited()


async def test_skips_non_ad_dm():
    checker = MagicMock()
    checker.fetch_inbound_dialogs.return_value = [_dialog(text="привет, как дела")]
    session = AsyncMock()
    session.execute = AsyncMock(return_value=MagicMock(rowcount=1))

    stats = await dm_scanner.scan_region_dialogs(
        session, checker, _REGION, classify_fn=_classify_not
    )
    assert stats["ads"] == 0
    assert stats["new"] == 0
    session.execute.assert_not_awaited()


async def test_dedup_existing_dm():
    checker = MagicMock()
    checker.fetch_inbound_dialogs.return_value = [_dialog()]
    session = AsyncMock()
    # ON CONFLICT DO NOTHING для уже существующего диалога → 0 затронутых строк.
    session.execute = AsyncMock(return_value=MagicMock(rowcount=0))

    stats = await dm_scanner.scan_region_dialogs(
        session, checker, _REGION, classify_fn=_classify_ad
    )
    assert stats["ads"] == 1
    assert stats["new"] == 0


async def test_insert_sets_can_message_true_for_user(monkeypatch):
    """Не-групповой автор написал первым → can_message=True проставляется в values."""
    captured = {}

    class _FakeInsert:
        def values(self, **kw):
            captured.update(kw)
            return self

        def on_conflict_do_nothing(self, **kw):
            return self

    monkeypatch.setattr(dm_scanner, "pg_insert", lambda model: _FakeInsert())
    session = AsyncMock()
    session.execute = AsyncMock(return_value=MagicMock(rowcount=1))

    await dm_scanner._insert_dm_if_new(session, _REGION, _dialog(), 5, ["x"])
    assert captured["origin"] == "inbound_dm"
    assert captured["vk_post_id"] is None
    assert captured["last_message_id"] == 999
    assert captured["can_message"] is True
    assert captured["can_message_checked_at"] is not None


async def test_insert_group_author_no_can_message(monkeypatch):
    """Автор-группа → can_message не предзаполняется (None)."""
    captured = {}

    class _FakeInsert:
        def values(self, **kw):
            captured.update(kw)
            return self

        def on_conflict_do_nothing(self, **kw):
            return self

    monkeypatch.setattr(dm_scanner, "pg_insert", lambda model: _FakeInsert())
    session = AsyncMock()
    session.execute = AsyncMock(return_value=MagicMock(rowcount=1))

    await dm_scanner._insert_dm_if_new(session, _REGION, _dialog(author_is_group=True), 5, ["x"])
    assert captured["author_is_group"] is True
    assert captured["can_message"] is None
    assert captured["can_message_checked_at"] is None
