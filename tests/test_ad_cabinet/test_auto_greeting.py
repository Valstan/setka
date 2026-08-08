"""Тесты авто-приветствия рекламодателю (улучшение отклика, run_auto_greeting).

VK-отправка инъектируется (send), сессия БД — фейковая (async CM). Текст и
allowlist передаём явно, чтобы не зависеть от env.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from database.models import AdRequest
from modules.ad_cabinet import auto_greeting as ag

_STATS = {"communities_count": 26, "subscribers_count": 29556}


class _FakeSession:
    """Сессия, возвращающая candidate-строки из любого execute()."""

    def __init__(self, candidate_rows, already_greeted_peer_ids=()):
        self._rows = candidate_rows
        self._already_greeted = list(already_greeted_peer_ids)
        self.add = MagicMock()
        self.commit = AsyncMock()

    async def execute(self, stmt):
        r = MagicMock()
        r.scalars.return_value.all.return_value = self._rows
        r.scalar.return_value = 1  # для func.count / func.sum
        # Анти-дубликат: запрос peer_id списком
        r.all.return_value = [(pid,) for pid in self._already_greeted]
        return r


class _FakeSessionCM:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        return False


_NOW = datetime(2026, 6, 13, 12, 0)


def _req(**kw):
    defaults = dict(
        id=1,
        community_vk_id=-100,
        peer_id=555,
        author_is_group=False,
        author_name="Иван",
        community_name="Гоньба",
        status="new",
        can_message=True,
        greeting_sent_at=None,
    )
    defaults.update(kw)
    return AdRequest(**defaults)


def _run(
    rows,
    send,
    allowlist={-100},
    template_text=(
        "Здравствуйте, {author_name}! {communities_count} групп, {subscribers_count} чел."
    ),
    now=_NOW,
    already_greeted_peer_ids=(),
):
    session = _FakeSession(rows, already_greeted_peer_ids=already_greeted_peer_ids)
    with patch.object(ag, "_get_network_stats", new=AsyncMock(return_value=_STATS)):
        out = asyncio.run(
            ag.run_auto_greeting(
                session_factory=lambda: _FakeSessionCM(session),
                send=send,
                allowlist=allowlist,
                template_text=template_text,
                now=now,
            )
        )
    return out, session


def test_greets_eligible_request():
    sent = []
    ar = _req()
    out, session = _run(
        [ar], send=lambda gid, pid, msg: sent.append((gid, pid, msg)) or {"success": True}
    )
    assert out == {"greeted": 1, "checked": 1}
    assert ar.greeting_sent_at == _NOW
    assert sent == [(-100, 555, "Здравствуйте, Иван! 26 групп, 29 556 чел.")]
    assert session.add.call_count >= 1
    session.commit.assert_awaited()


def test_disabled_when_allowlist_empty():
    sent = []
    out, session = _run(
        [_req()], send=lambda *a: sent.append(a) or {"success": True}, allowlist=set()
    )
    assert out == {"greeted": 0, "checked": 0, "skipped": "disabled"}
    assert not sent


def test_allow_all_greets_any_community():
    sent = []
    ar = _req(community_vk_id=-999)
    session = _FakeSession([ar])
    with patch.object(ag, "_get_network_stats", new=AsyncMock(return_value=_STATS)):
        out = asyncio.run(
            ag.run_auto_greeting(
                session_factory=lambda: _FakeSessionCM(session),
                send=lambda gid, pid, msg: sent.append((gid, pid, msg)) or {"success": True},
                allow_all=True,
                template_text="Здравствуйте, {author_name}!",
                now=_NOW,
            )
        )
    assert out == {"greeted": 1, "checked": 1}
    assert sent == [(-999, 555, "Здравствуйте, Иван!")]


def test_env_allow_all_parsing(monkeypatch):
    monkeypatch.setenv("AD_AUTO_GREETING_COMMUNITIES", "*")
    assert ag._env_allow_all() is True
    monkeypatch.setenv("AD_AUTO_GREETING_COMMUNITIES", "  ALL ")
    assert ag._env_allow_all() is True
    monkeypatch.setenv("AD_AUTO_GREETING_COMMUNITIES", "-100,-200")
    assert ag._env_allow_all() is False
    assert ag._env_allowlist() == {-100, -200}


def test_skips_group_author():
    sent = []
    ar = _req(author_is_group=True)
    out, _ = _run([ar], send=lambda *a: sent.append(a) or {"success": True})
    assert out["greeted"] == 0
    assert not sent
    assert ar.greeting_sent_at is None


def test_skips_when_no_peer():
    sent = []
    ar = _req(peer_id=0)
    out, _ = _run([ar], send=lambda *a: sent.append(a) or {"success": True})
    assert out["greeted"] == 0
    assert not sent


def test_send_failure_not_marked():
    ar = _req()
    out, _ = _run([ar], send=lambda gid, pid, msg: {"success": False})
    assert out["greeted"] == 0
    assert ar.greeting_sent_at is None


def test_mangled_template_blocks_sending():
    """Шаблон с потерянной кодировкой не уходит рекламодателю (инцидент 2026-08-07)."""
    sent = []
    ar = _req()
    out, session = _run(
        [ar],
        send=lambda gid, pid, msg: sent.append((gid, pid, msg)) or {"success": True},
        template_text="????????????, {author_name}! ??????? ?? ???? ???????????.",
    )
    assert out == {"greeted": 0, "checked": 0, "skipped": "mangled_template"}
    assert not sent
    assert ar.greeting_sent_at is None
    session.commit.assert_not_awaited()


def test_empty_rows_noop():
    out, session = _run([], send=lambda *a: {"success": True})
    assert out == {"greeted": 0, "checked": 0}
    session.commit.assert_not_awaited()


def test_skips_already_greeted_peer():
    """Повторно в тот же диалог (тот же peer_id) автоответ не шлём."""
    sent = []
    ar = _req(id=2, peer_id=555)  # новый запрос, но тот же peer_id
    out, _ = _run(
        [ar],
        send=lambda gid, pid, msg: sent.append((gid, pid, msg)) or {"success": True},
        already_greeted_peer_ids=(555,),
    )
    assert out["greeted"] == 0
    assert not sent
    assert ar.greeting_sent_at is None


def test_does_not_skip_different_peer():
    """Другому peer_id приветствие уходит."""
    sent = []
    ar = _req(id=3, peer_id=777)
    out, _ = _run(
        [ar],
        send=lambda gid, pid, msg: sent.append((gid, pid, msg)) or {"success": True},
        already_greeted_peer_ids=(555,),  # другой peer_id, не 777
    )
    assert out["greeted"] == 1
    assert len(sent) == 1
    assert ar.greeting_sent_at == _NOW
