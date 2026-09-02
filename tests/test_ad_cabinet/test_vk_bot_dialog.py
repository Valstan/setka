"""ВК-бот кабинета, этап 2 — диалог и Long Poll-тик (vk_bot/dialog, vk_bot/intake).

Настоящая in-memory БД (conftest), сеть и Redis — двойники. Что охраняется:
незнакомый vk_id получает карточку автоматически (решение владельца), кнопки
меню работают из любого шага, заказ доходит до ``submit`` ровно с тем, что
выбрал клиент, отказ ``OrderError`` не роняет диалог, состояние сбрасывается
после завершения, тик обрабатывает только ``message_new`` от людей.
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest
from sqlalchemy import select

from database.models import AdClient, AdInteraction, Region
from database.models_extended import RadarUser
from modules.ad_cabinet import client_orders
from modules.ad_cabinet.vk_bot import dialog, intake

NOW = datetime(2026, 9, 2, 12, 0)


async def _region(session, name, gid):
    r = Region(name=name, code=name.lower(), vk_group_id=gid, is_active=True)
    session.add(r)
    await session.flush()
    return r


class _Submit:
    def __init__(self, result=None, error=None):
        self.calls = []
        self.result = result or {"order_ref": "r1", "price_total": 700.0, "posts": [1, 2], "moderation": True}
        self.error = error

    async def __call__(self, session, client, draft):
        self.calls.append((client.id, dict(draft)))
        if self.error:
            raise self.error
        return self.result


def _btn(cmd):
    return dialog.Incoming(peer_id=500, payload={"cmd": cmd})


def _msg(text):
    return dialog.Incoming(peer_id=500, text=text)


# ───────── чистые ─────────


def test_command_from_payload_and_text():
    assert dialog.Incoming(peer_id=1, payload={"cmd": "prices"}).command() == "prices"
    assert dialog.Incoming(peer_id=1, text="📋 Цены").command() == "prices"
    assert dialog.Incoming(peer_id=1, text="отмена").command() == "cancel"
    assert dialog.Incoming(peer_id=1, text="привет").command() is None


def test_parse_payload():
    assert dialog.parse_payload('{"cmd": "x"}') == {"cmd": "x"}
    assert dialog.parse_payload("не json") is None
    assert dialog.parse_payload({"cmd": "y"}) == {"cmd": "y"}


def test_parse_region_choice():
    regions = [(10, "А"), (20, "Б"), (30, "В")]
    assert dialog.parse_region_choice("1, 3", regions) == [10, 30]
    assert dialog.parse_region_choice("2 2 9", regions) == [20]
    assert dialog.parse_region_choice("все", regions) == []


def test_parse_when():
    assert dialog.parse_when("25.09 14:30", now_msk=NOW) == datetime(2026, 9, 25, 14, 30)
    assert dialog.parse_when("завтра 10:00", now_msk=NOW) == datetime(2026, 9, 3, 10, 0)
    assert dialog.parse_when("31.02 10:00", now_msk=NOW) is None
    assert dialog.parse_when("когда-нибудь", now_msk=NOW) is None


def test_keyboard_is_valid_vk_json():
    kb = json.loads(dialog.MAIN_KEYBOARD)
    labels = [b["action"]["label"] for row in kb["buttons"] for b in row]
    assert "🛒 Заказать пост" in labels
    for label in labels:
        assert label in dialog.BUTTON_TEXT


def test_extract_incoming_filters_non_messages():
    assert intake.extract_incoming({"type": "message_reply"}) is None
    assert intake.extract_incoming({"type": "message_new", "object": {"message": {"from_id": -5}}}) is None
    inc = intake.extract_incoming(
        {
            "type": "message_new",
            "object": {"message": {"from_id": 7, "text": "hi", "payload": '{"cmd":"pay"}'}},
        }
    )
    assert inc.peer_id == 7 and inc.command() == "pay"


# ───────── карточка ─────────


@pytest.mark.asyncio
async def test_unknown_vk_id_gets_a_card_and_signup_event(db_session):
    async def name_fetch(vk_id):
        return "Иван Тестов"

    replies, state, events = await dialog.handle(
        db_session, _msg("привет"), None, submit=_Submit(), name_fetch=name_fetch, now_msk=NOW
    )
    assert state is None and events == ["signup"]
    client = (await db_session.execute(select(AdClient))).scalar_one()
    assert client.author_vk_id == 500 and client.name == "Иван Тестов" and client.trusted is False
    assert f"№{client.id}" in replies[0][0] and replies[0][1] == dialog.MAIN_KEYBOARD
    rows = (await db_session.execute(select(AdInteraction))).scalars().all()
    assert rows[0].kind == "cabinet_signup" and rows[0].meta_json["source"] == "vk_bot"


@pytest.mark.asyncio
async def test_known_vk_account_is_found_not_duplicated(db_session):
    u = RadarUser(role="advertiser", vk_user_id=500)
    db_session.add(u)
    await db_session.flush()
    db_session.add(AdClient(name="Свой", radar_user_id=u.id))
    await db_session.flush()

    _r, _s, events = await dialog.handle(db_session, _msg("hi"), None, submit=_Submit(), now_msk=NOW)
    assert events == []
    assert len((await db_session.execute(select(AdClient))).scalars().all()) == 1


# ───────── меню ─────────


@pytest.mark.asyncio
async def test_menu_buttons_work_from_any_step(db_session):
    replies, state, _ = await dialog.handle(
        db_session, _btn("prices"), {"step": "order_text", "draft": {}}, submit=_Submit(), now_msk=NOW
    )
    assert state is None and "Цены" in replies[0][0]
    replies, state, _ = await dialog.handle(db_session, _btn("pay"), None, submit=_Submit(), now_msk=NOW)
    assert "Альфа" in replies[0][0]
    replies, state, _ = await dialog.handle(db_session, _btn("balance"), None, submit=_Submit(), now_msk=NOW)
    assert "Оплачено" in replies[0][0]


@pytest.mark.asyncio
async def test_chat_flow(db_session):
    from database.models import AdChatMessage

    _r, state, _ = await dialog.handle(db_session, _btn("chat"), None, submit=_Submit(), now_msk=NOW)
    assert state["step"] == "chat"
    replies, state, events = await dialog.handle(
        db_session, _msg("Хочу пост про магазин"), state, submit=_Submit(), now_msk=NOW
    )
    assert state is None and "chat" in events
    msg = (await db_session.execute(select(AdChatMessage))).scalar_one()
    assert msg.sender == "client" and "магазин" in msg.body


# ───────── заказ ─────────


@pytest.mark.asyncio
async def test_order_flow_reaches_submit_with_chosen_regions(db_session):
    r1 = await _region(db_session, "Арбаж", -1)
    r2 = await _region(db_session, "Уржум", -2)
    await _region(db_session, "Нема", -3)
    s = _Submit()

    _r, state, _ = await dialog.handle(db_session, _btn("order"), None, submit=s, now_msk=NOW)
    assert state["step"] == "order_text"
    replies, state, _ = await dialog.handle(db_session, _msg("Продам дрова"), state, submit=s, now_msk=NOW)
    assert state["step"] == "order_regions" and "1. Арбаж" in replies[0][0]
    _r, state, _ = await dialog.handle(db_session, _msg("1, 3"), state, submit=s, now_msk=NOW)
    assert state["step"] == "order_when" and state["draft"]["region_ids"] == [r1.id, r2.id]
    replies, state, _ = await dialog.handle(db_session, _msg("25.09 14:30"), state, submit=s, now_msk=NOW)
    assert state["step"] == "order_confirm" and "районов: 2" in replies[0][0]
    replies, state, events = await dialog.handle(db_session, _btn("confirm"), state, submit=s, now_msk=NOW)

    assert state is None and "order" in events
    assert s.calls and s.calls[0][1]["text"] == "Продам дрова"
    assert s.calls[0][1]["publish_at"] == "2026-09-25T14:30:00" and s.calls[0][1]["publish_now"] is False
    assert "Владелец проверит" in replies[0][0]
    kinds = [r.kind for r in (await db_session.execute(select(AdInteraction))).scalars().all()]
    assert "client_order" in kinds


@pytest.mark.asyncio
async def test_order_all_regions_and_now(db_session):
    await _region(db_session, "А", -1)
    await _region(db_session, "Б", -2)
    s = _Submit()
    _r, state, _ = await dialog.handle(db_session, _btn("order"), None, submit=s, now_msk=NOW)
    _r, state, _ = await dialog.handle(db_session, _msg("текст"), state, submit=s, now_msk=NOW)
    _r, state, _ = await dialog.handle(db_session, _btn("all_regions"), state, submit=s, now_msk=NOW)
    assert len(state["draft"]["region_ids"]) == 2
    _r, state, _ = await dialog.handle(db_session, _btn("now"), state, submit=s, now_msk=NOW)
    assert state["draft"]["publish_now"] is True
    await dialog.handle(db_session, _btn("confirm"), state, submit=s, now_msk=NOW)
    assert s.calls[0][1]["publish_now"] is True


@pytest.mark.asyncio
async def test_order_error_is_reported_and_state_reset(db_session):
    await _region(db_session, "А", -1)
    s = _Submit(error=client_orders.OrderError("Пакет исчерпан"))
    state = {"step": "order_confirm", "draft": {"text": "t", "region_ids": [1], "publish_now": True}}
    replies, state, events = await dialog.handle(db_session, _btn("confirm"), state, submit=s, now_msk=NOW)
    assert state is None and "Пакет исчерпан" in replies[0][0] and "order" not in events
    kinds = [r.kind for r in (await db_session.execute(select(AdInteraction))).scalars().all()]
    assert "cabinet_order_refused" in kinds


@pytest.mark.asyncio
async def test_cancel_resets_from_any_step(db_session):
    replies, state, _ = await dialog.handle(
        db_session, _msg("отмена"), {"step": "order_when", "draft": {"x": 1}}, submit=_Submit(), now_msk=NOW
    )
    assert state is None and replies[0][1] == dialog.MAIN_KEYBOARD


# ───────── тик ─────────


@pytest.mark.asyncio
async def test_poll_once_runs_dialog_and_persists_state(db_session, monkeypatch):
    states = {}
    sent = []

    def api_call(token, method, **params):
        if method == "groups.getLongPollServer":
            return {"response": {"server": "https://lp", "key": "k", "ts": "1"}}
        if method == "users.get":
            return {"response": [{"first_name": "Пётр", "last_name": "Иванов"}]}
        return {}

    def lp_get(server, key, ts):
        return {
            "ts": "2",
            "updates": [
                {"type": "message_new", "object": {"message": {"from_id": 900, "text": "", "payload": '{"cmd":"chat"}'}}},
                {"type": "message_reply", "object": {}},
            ],
        }

    async def sender(peer_id, text, kb=None):
        sent.append((peer_id, text, kb))
        return {"response": 1}

    class _Factory:
        def __call__(self):
            return self

        async def __aenter__(self):
            return db_session

        async def __aexit__(self, *a):
            return False

    ts = {}
    pings = []

    async def fake_owner(text, **kw):
        pings.append(text)
        return {}

    monkeypatch.setattr("modules.ad_cabinet.vk_bot.notify.notify_owner", fake_owner)

    out = await intake.poll_once(
        token="t",
        group_id=1,
        session_factory=_Factory(),
        state_get=states.get,
        state_set=lambda p, s: states.__setitem__(p, s),
        ts_get=lambda: ts.get("ts"),
        ts_set=lambda v: ts.__setitem__("ts", v),
        submit=_Submit(),
        api_call=api_call,
        lp_get=lp_get,
        sender=sender,
    )
    assert out["ok"] and out["processed"] == 1 and ts["ts"] == "2"
    assert states[900]["step"] == "chat"
    assert sent and sent[0][0] == 900 and sent[0][1].startswith("Напишите сообщение")
    client = (await db_session.execute(select(AdClient))).scalar_one()
    assert client.name == "Пётр Иванов"
    assert any("новый клиент" in p for p in pings)


def test_redis_state_store_roundtrip():
    store = {}

    class R:
        def get(self, k):
            return store.get(k)

        def set(self, k, v, ex=None):
            store[k] = v

        def delete(self, k):
            store.pop(k, None)

    get, set_ = intake.redis_state_store(R())
    set_(5, {"step": "chat", "draft": {}})
    assert get(5) == {"step": "chat", "draft": {}}
    set_(5, None)
    assert get(5) is None


def test_task_registered():
    from tasks.celery_app import app
    import tasks.vk_bot_tasks  # noqa: F401

    assert "tasks.vk_bot_tasks.poll_sarafan_vk_bot" in app.tasks
    assert app.conf.beat_schedule["sarafan-vk-bot"]["task"] == "tasks.vk_bot_tasks.poll_sarafan_vk_bot"
