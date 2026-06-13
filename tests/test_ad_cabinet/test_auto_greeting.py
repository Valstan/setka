"""Тесты авто-приветствия рекламодателю (улучшение отклика, run_auto_greeting).

VK-отправка инъектируется (send), сессия БД — фейковая (async CM). Текст и
allowlist передаём явно, чтобы не зависеть от env.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

from database.models import AdRequest
from modules.ad_cabinet import auto_greeting as ag


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows
        self.add = MagicMock()
        self.commit = AsyncMock()

    async def execute(self, stmt):
        r = MagicMock()
        r.scalars.return_value.all.return_value = self._rows
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


def _run(rows, send, allowlist={-100}, template_text="Здравствуйте, {author_name}!", now=_NOW):
    session = _FakeSession(rows)
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
    assert sent == [(-100, 555, "Здравствуйте, Иван!")]  # плейсхолдер подставлен
    assert session.add.call_count >= 1  # событие в таймлайн
    session.commit.assert_awaited()


def test_disabled_when_allowlist_empty():
    sent = []
    out, session = _run(
        [_req()], send=lambda *a: sent.append(a) or {"success": True}, allowlist=set()
    )
    assert out == {"greeted": 0, "checked": 0, "skipped": "disabled"}
    assert not sent


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


def test_empty_rows_noop():
    out, session = _run([], send=lambda *a: {"success": True})
    assert out == {"greeted": 0, "checked": 0}
    session.commit.assert_not_awaited()
