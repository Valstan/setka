"""Tests for Flow B — Гоньба VK wall → Telegram mirror."""

import time
from types import SimpleNamespace
from unittest.mock import MagicMock

from modules.telegram_gonba_mirror import execute_gonba_telegram_mirror
from utils.post_utils import lip_of_post

VK_ID = -218688001


class _Result:
    def __init__(self, first=None):
        self._first = first

    def scalars(self):
        m = MagicMock()
        m.first.return_value = self._first
        return m


class _FakeSession:
    """Returns queued results per execute() call; records commits."""

    def __init__(self, results):
        self._results = list(results)
        self.commits = 0

    async def execute(self, stmt):
        return self._results.pop(0)

    async def commit(self):
        self.commits += 1

    async def refresh(self, obj):
        return None

    def add(self, obj):
        return None


class _FakeVK:
    def __init__(self, posts):
        self._posts = posts

    def get_wall_posts(self, owner_id, count, offset):
        return self._posts


class _FakeVKAsyncCM:
    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def _make_request(self, method, params):
        return {"items": []}


def _community(**over):
    base = dict(id=847, vk_id=VK_ID, telegram_channel="@gonba_life", telegram_bot="VALSTANBOT")
    base.update(over)
    return SimpleNamespace(**base)


def _patch_common(monkeypatch, posts, wt, sent_calls):
    async def _tokens(session):
        return {"T": "tok"}

    monkeypatch.setattr("modules.vk_token_router.get_active_parse_tokens", _tokens)
    monkeypatch.setattr("modules.vk_monitor.vk_client.VKClient", lambda tok: _FakeVK(posts))
    monkeypatch.setattr("modules.vk_monitor.vk_client_async.VKClientAsync", _FakeVKAsyncCM)
    monkeypatch.setattr(
        "modules.publisher.telegram_repost_config.telegram_repost_disabled", lambda: False
    )

    async def _fake_repost(bot, channel, text, media, *, test_mode=False):
        sent_calls.append({"bot": bot, "channel": channel, "text": text})
        return {"success": True}

    monkeypatch.setattr("modules.publisher.telegram_repost.repost_to_telegram", _fake_repost)


async def test_gonba_mirrors_only_fresh_non_ad(monkeypatch):
    now = int(time.time())
    seen_lip = lip_of_post(VK_ID, 1)
    posts = [
        {"id": 1, "owner_id": VK_ID, "date": now - 100, "text": "уже виденный"},
        {"id": 2, "owner_id": VK_ID, "date": now - 10 * 24 * 3600, "text": "старый"},
        {
            "id": 3,
            "owner_id": VK_ID,
            "date": now - 200,
            "text": "купить скидка заказать" " цена: 100 звоните: 999 whatsapp",
            "marked_as_ads": True,
        },
        {"id": 4, "owner_id": VK_ID, "date": now - 300, "text": "Свежая новость одна"},
        {"id": 5, "owner_id": VK_ID, "date": now - 50, "text": "Свежая новость два"},
    ]
    wt = SimpleNamespace(lip=[seen_lip], hash=[])
    sent = []
    _patch_common(monkeypatch, posts, wt, sent)
    session = _FakeSession([_Result(_community()), _Result(wt)])

    res = await execute_gonba_telegram_mirror(session, test_mode=False)

    assert res["success"] is True
    # posts 1 (seen), 2 (old), 3 (ad) filtered; 4 & 5 sent, oldest-first.
    assert [c["text"] for c in sent] == ["Свежая новость одна", "Свежая новость два"]
    assert all(c["bot"] == "VALSTANBOT" and c["channel"] == "@gonba_life" for c in sent)
    assert res["stats"]["sent"] == 2
    assert res["stats"]["skipped_seen"] == 1
    assert res["stats"]["skipped_old"] == 1
    assert res["stats"]["skipped_ads"] == 1
    # lip history advanced with sent posts (and ad lip marked seen).
    assert lip_of_post(VK_ID, 4) in wt.lip
    assert lip_of_post(VK_ID, 5) in wt.lip
    assert lip_of_post(VK_ID, 3) in wt.lip  # ad marked seen
    assert session.commits >= 1


async def test_gonba_respects_cap(monkeypatch):
    now = int(time.time())
    posts = [
        {"id": i, "owner_id": VK_ID, "date": now - i, "text": f"новость {i}"} for i in range(1, 6)
    ]
    wt = SimpleNamespace(lip=[], hash=[])
    sent = []
    _patch_common(monkeypatch, posts, wt, sent)
    monkeypatch.setattr(
        "modules.publisher.telegram_repost_config.get_gonba_max_posts_per_run", lambda: 2
    )
    session = _FakeSession([_Result(_community()), _Result(wt)])

    res = await execute_gonba_telegram_mirror(session)
    assert res["stats"]["sent"] == 2  # capped


async def test_gonba_no_channel_configured(monkeypatch):
    session = _FakeSession([_Result(_community(telegram_channel=None, telegram_bot=None))])
    res = await execute_gonba_telegram_mirror(session)
    assert res["success"] is False
    assert "telegram_channel" in res["error"]


async def test_gonba_disabled(monkeypatch):
    monkeypatch.setenv("TELEGRAM_REPOST_DISABLED", "1")
    session = _FakeSession([])  # short-circuits before any query
    res = await execute_gonba_telegram_mirror(session)
    assert res.get("skipped") == "disabled"
