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


# ───────── фото в ЛС (Этап 5) ─────────


class _Sender4:
    """Двойник с четвёртым аргументом — вложением."""

    def __init__(self, resp=None):
        self.calls = []
        self.resp = resp if resp is not None else {"response": 1}

    async def __call__(self, peer_id, text, keyboard=None, attachment=None):
        self.calls.append((peer_id, text, keyboard, attachment))
        return self.resp


@pytest.mark.asyncio
async def test_notify_client_passes_attachment_only_when_given(db_session):
    u = await _user(db_session, vk_user_id=777)
    c = await _client(db_session, radar_user_id=u.id)
    s = _Sender4()
    assert await notify.notify_client(db_session, c.id, "x", sender=s, attachment="photo-5_99")
    assert s.calls == [(777, "x", None, "photo-5_99")]
    strict = _Sender()  # трёхаргументный двойник: без вложения лишнего не передаём
    assert await notify.notify_client(db_session, c.id, "y", sender=strict)
    assert strict.calls == [(777, "y", None)]
    rows = (await db_session.execute(select(AdInteraction).order_by(AdInteraction.id))).scalars()
    summaries = [r.summary for r in rows]
    assert "(с фото)" in summaries[0] and "(с фото)" not in summaries[1]


@pytest.mark.asyncio
async def test_notify_client_uploads_named_photos(db_session, monkeypatch):
    u = await _user(db_session, vk_user_id=777)
    c = await _client(db_session, radar_user_id=u.id)
    got = {}

    async def fake_upload(token, client_id, names, *, peer_id=None, group_id=None):
        got.update(token=token, client_id=client_id, names=list(names), peer_id=peer_id)
        return "photo-5_1"

    monkeypatch.setattr(notify, "upload_client_photos", fake_upload)
    s = _Sender4()
    assert await notify.notify_client(db_session, c.id, "x", sender=s, photos=["a.jpg"])
    assert s.calls[0][3] == "photo-5_1"
    assert got == {"token": None, "client_id": c.id, "names": ["a.jpg"], "peer_id": 777}

    # заливка не удалась — текст всё равно уходит, без вложения
    async def none_upload(*a, **k):
        return None

    monkeypatch.setattr(notify, "upload_client_photos", none_upload)
    s2 = _Sender()
    assert await notify.notify_client(db_session, c.id, "x", sender=s2, photos=["a.jpg"])
    assert s2.calls == [(777, "x", None)]


@pytest.mark.asyncio
async def test_upload_client_photos_uses_community_token(monkeypatch, tmp_path):
    monkeypatch.setenv("AD_UPLOAD_DIR", str(tmp_path))
    d = tmp_path / "9"
    d.mkdir()
    for n in ("a.jpg", "b.jpg", "c.jpg", "d.jpg", "e.jpg", "f.jpg"):
        (d / n).write_bytes(n.encode())
    calls = {}

    class _Api:
        pass

    class _VkApi:
        def __init__(self, token):
            calls["token"] = token

        def get_api(self):
            return _Api()

    import vk_api

    monkeypatch.setattr(vk_api, "VkApi", _VkApi)

    def fake_upload(api, images, *, peer_id=None):
        calls["images"] = images
        calls["peer_id"] = peer_id
        return "photo-5_1,photo-5_2"

    monkeypatch.setattr(notify.vk_photo_upload, "upload_offer_images", fake_upload)
    names = ["b.jpg", "a.jpg", "zzz.jpg", "c.jpg", "d.jpg", "e.jpg", "f.jpg"]
    att = await notify.upload_client_photos("T", 9, names, peer_id=77)
    assert att == "photo-5_1,photo-5_2" and calls["token"] == "T" and calls["peer_id"] == 77
    # не больше 5 (лимит ЛС), пропавшие пропущены, порядок — каталога
    assert calls["images"] == [b"a.jpg", b"b.jpg", b"c.jpg", b"d.jpg", b"e.jpg"]
    assert await notify.upload_client_photos(None, 9, ["a.jpg"]) is None
    assert await notify.upload_client_photos("T", 9, []) is None
    assert await notify.upload_client_photos("T", 9, ["zzz.jpg"]) is None

    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(notify.vk_photo_upload, "upload_offer_images", boom)
    assert await notify.upload_client_photos("T", 9, ["a.jpg"]) is None


@pytest.mark.asyncio
async def test_make_sender_passes_attachment(monkeypatch):
    posted = []

    class _Resp:
        def json(self):
            return {"response": 1}

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, data=None):
            posted.append(data)
            return _Resp()

    monkeypatch.setattr(notify.httpx, "AsyncClient", _Client)
    import modules.vk_monitor.vk_client as vkc

    monkeypatch.setattr(vkc, "enforce_token_rate_limit", lambda token, method="": None)
    from modules.ad_cabinet import dm_channel

    monkeypatch.setattr(dm_channel, "paused_until", lambda cid, **kw: None)
    send = notify._make_sender("T", 1)
    await send(7, "txt", None, "photo-5_99")
    await send(7, "txt")
    assert posted[0]["attachment"] == "photo-5_99" and "attachment" not in posted[1]


@pytest.mark.asyncio
async def test_notify_client_photos_via_community_token(db_session, monkeypatch):
    """Прод-путь без sender=: токен/group_id САРАФАНа доходят до заливки, вложение — до vk_send."""
    u = await _user(db_session, vk_user_id=777)
    c = await _client(db_session, radar_user_id=u.id)

    async def conf():
        return (241, "T")

    monkeypatch.setattr(notify, "community", conf)
    got, sent = {}, []

    async def fake_upload(token, client_id, names, *, peer_id=None, group_id=None):
        got.update(token=token, peer_id=peer_id, group_id=group_id, names=list(names))
        return "photo-1_2"

    async def fake_send(token, group_id, peer_id, text, keyboard=None, attachment=None):
        sent.append((token, group_id, peer_id, attachment))
        return {"response": 1}

    monkeypatch.setattr(notify, "upload_client_photos", fake_upload)
    monkeypatch.setattr(notify, "vk_send", fake_send)
    assert await notify.notify_client(db_session, c.id, "x", photos=["a.jpg"]) is True
    assert got == {"token": "T", "peer_id": 777, "group_id": 241, "names": ["a.jpg"]}
    assert sent == [("T", 241, 777, "photo-1_2")]

    # бот выключен — тишина без обращения к заливке
    async def off():
        return None

    monkeypatch.setattr(notify, "community", off)
    got.clear()
    assert await notify.notify_client(db_session, c.id, "x", photos=["a.jpg"]) is False
    assert got == {}


@pytest.mark.asyncio
async def test_upload_client_photos_skips_paused_channel_and_caches(monkeypatch, tmp_path):
    monkeypatch.setenv("AD_UPLOAD_DIR", str(tmp_path))
    (tmp_path / "9").mkdir()
    (tmp_path / "9" / "a.jpg").write_bytes(b"a")
    from modules.ad_cabinet import dm_channel

    monkeypatch.setattr(dm_channel, "paused_until", lambda cid, **kw: 1 if cid == 5 else None)
    import vk_api

    _api = type("A", (), {"get_api": lambda s: None})
    monkeypatch.setattr(vk_api, "VkApi", lambda token: _api())
    n = {"uploads": 0}

    def fake_upload(api, images, *, peer_id=None):
        n["uploads"] += 1
        return "photo-9_1"

    monkeypatch.setattr(notify.vk_photo_upload, "upload_offer_images", fake_upload)
    notify._ATTACHMENT_CACHE.clear()
    # пауза канала — заливки нет
    assert await notify.upload_client_photos("T", 9, ["a.jpg"], peer_id=7, group_id=5) is None
    assert n["uploads"] == 0
    # живой канал — одна заливка на повторные вызовы (кэш по client/имена/peer)
    assert (
        await notify.upload_client_photos("T", 9, ["a.jpg"], peer_id=7, group_id=6) == "photo-9_1"
    )
    assert (
        await notify.upload_client_photos("T", 9, ["a.jpg"], peer_id=7, group_id=6) == "photo-9_1"
    )
    assert n["uploads"] == 1
    assert await notify.upload_client_photos("T", 9, ["a.jpg"], peer_id=8) == "photo-9_1"
    assert n["uploads"] == 2  # другой peer — своя заливка
    notify._ATTACHMENT_CACHE.clear()
