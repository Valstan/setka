"""ВК-бот кабинета, этап 1 — исходящие уведомления (modules/ad_cabinet/vk_bot/notify).

Сеть инъектируется (``sender``), БД — настоящая in-memory (conftest). Что
охраняется: адресат клиента (аккаунт с ВК-входом → автор предложки → никому,
сообществу в личку не пишем); «не разрешил сообщения» — тишина, не ошибка;
успешная отправка оставляет след в журнале; владелец получает оба канала с
одним дедупом.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from database.models import AdClient, AdInteraction
from database.models_extended import RadarUser
from modules.ad_cabinet.vk_bot import notify


class _Sender:
    """Двойник messages.send: запоминает вызовы, отвечает по сценарию."""

    def __init__(self, resp=None):
        self.calls = []
        self.resp = resp if resp is not None else {"response": 1}

    async def __call__(self, peer_id, text, keyboard=None):
        self.calls.append((peer_id, text, keyboard))
        return self.resp


async def _user(session, **kw):
    u = RadarUser(role="advertiser", **kw)
    session.add(u)
    await session.flush()
    return u


async def _client(session, **kw):
    c = AdClient(**kw)
    session.add(c)
    await session.flush()
    return c


# ───────── чистые функции ─────────


def test_client_peer_id_prefers_linked_vk_account():
    c = AdClient(author_vk_id=555)
    u = RadarUser(vk_user_id=777)
    assert notify.client_peer_id(c, u) == 777
    assert notify.client_peer_id(c, None) == 555


def test_client_peer_id_never_targets_a_group():
    assert notify.client_peer_id(AdClient(author_vk_id=-123), None) is None
    assert notify.client_peer_id(AdClient(author_vk_id=123, author_is_group=True), None) is None
    assert notify.client_peer_id(AdClient(), RadarUser(login="x")) is None


def test_send_outcome():
    assert notify.send_outcome({"response": 42}) == (True, None)
    assert notify.send_outcome({"error": {"error_code": 901}}) == (False, 901)
    assert notify.send_outcome({}) == (False, None)
    assert notify.send_outcome(None) == (False, None)


# ───────── клиенту ─────────


@pytest.mark.asyncio
async def test_notify_client_sends_and_logs(db_session):
    u = await _user(db_session, vk_user_id=777)
    c = await _client(db_session, name="Петров", radar_user_id=u.id)
    s = _Sender()

    ok = await notify.notify_client(db_session, c.id, "привет", sender=s)
    await db_session.flush()

    assert ok is True
    assert s.calls == [(777, "привет", None)]
    rows = (await db_session.execute(select(AdInteraction))).scalars().all()
    assert [r.kind for r in rows] == [notify.KIND_NOTICE]
    assert rows[0].client_id == c.id and rows[0].actor == "system"


@pytest.mark.asyncio
async def test_notify_client_silent_when_not_allowed(db_session):
    """901 — клиент не разрешил сообщения сообществу: тишина, журнал пуст."""
    u = await _user(db_session, vk_user_id=777)
    c = await _client(db_session, radar_user_id=u.id)
    s = _Sender({"error": {"error_code": 901, "error_msg": "no permission"}})

    assert await notify.notify_client(db_session, c.id, "x", sender=s) is False
    rows = (await db_session.execute(select(AdInteraction))).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_notify_client_without_vk_target_is_noop(db_session):
    c = await _client(db_session, name="Без ВК")
    s = _Sender()
    assert await notify.notify_client(db_session, c.id, "x", sender=s) is False
    assert s.calls == []


@pytest.mark.asyncio
async def test_notify_client_log_false_leaves_journal_clean(db_session):
    u = await _user(db_session, vk_user_id=1)
    c = await _client(db_session, radar_user_id=u.id)
    assert await notify.notify_client(db_session, c.id, "x", sender=_Sender(), log=False)
    assert (await db_session.execute(select(AdInteraction))).scalars().all() == []


@pytest.mark.asyncio
async def test_notify_client_missing_client(db_session):
    assert await notify.notify_client(db_session, 999999, "x", sender=_Sender()) is False


# ───────── владельцу ─────────


@pytest.mark.asyncio
async def test_notify_owner_vk_goes_to_owner_ids(monkeypatch):
    monkeypatch.setenv("SETKA_OWNER_VK_IDS", "111,222")
    s = _Sender()
    out = await notify.notify_owner("пинг", sender=s, telegram=False)
    assert out["vk"] is True
    assert sorted(c[0] for c in s.calls) == [111, 222]


@pytest.mark.asyncio
async def test_notify_owner_dedup_blocks_both_channels(monkeypatch):
    monkeypatch.setenv("SETKA_OWNER_VK_IDS", "111")
    from modules.ad_cabinet import owner_ping

    monkeypatch.setattr(owner_ping, "ping_dedup_pass", lambda key, *, ttl: False)
    s = _Sender()
    out = await notify.notify_owner("пинг", dedup_key="k", sender=s, telegram=False)
    assert out == {"telegram": False, "vk": False}
    assert s.calls == []


@pytest.mark.asyncio
async def test_notify_owner_not_allowed_is_quiet(monkeypatch):
    monkeypatch.setenv("SETKA_OWNER_VK_IDS", "111")
    s = _Sender({"error": {"error_code": 901}})
    out = await notify.notify_owner("пинг", sender=s, telegram=False)
    assert out["vk"] is False


def test_owner_vk_ids_default_is_valstan(monkeypatch):
    monkeypatch.delenv("SETKA_OWNER_VK_IDS", raising=False)
    assert notify.owner_vk_ids() == (20002978,)


def test_community_off_without_env(monkeypatch):
    monkeypatch.delenv("SARAFAN_VK_COMMUNITY_ID", raising=False)
    from config import runtime

    assert runtime.get_sarafan_vk_community_id() is None
    monkeypatch.setenv("SARAFAN_VK_COMMUNITY_ID", "-12345")
    assert runtime.get_sarafan_vk_community_id() == 12345
