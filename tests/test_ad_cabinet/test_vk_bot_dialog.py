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
        self.result = result or {
            "order_ref": "r1",
            "price_total": 700.0,
            "posts": [1, 2],
            "moderation": True,
        }
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
    assert (
        intake.extract_incoming({"type": "message_new", "object": {"message": {"from_id": -5}}})
        is None
    )
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

    _r, _s, events = await dialog.handle(
        db_session, _msg("hi"), None, submit=_Submit(), now_msk=NOW
    )
    # Не signup: карточка найдена по аккаунту; текст известного клиента вне шага
    # уходит владельцу в чат (PR 1.7), а не в приветствие.
    assert events == ["chat"]
    assert len((await db_session.execute(select(AdClient))).scalars().all()) == 1


# ───────── меню ─────────


@pytest.mark.asyncio
async def test_menu_buttons_work_from_any_step(db_session):
    replies, state, _ = await dialog.handle(
        db_session,
        _btn("prices"),
        {"step": "order_text", "draft": {}},
        submit=_Submit(),
        now_msk=NOW,
    )
    assert state is None and "Цены" in replies[0][0]
    replies, state, _ = await dialog.handle(
        db_session, _btn("pay"), None, submit=_Submit(), now_msk=NOW
    )
    assert "Альфа" in replies[0][0]
    replies, state, _ = await dialog.handle(
        db_session, _btn("balance"), None, submit=_Submit(), now_msk=NOW
    )
    assert "Оплачено" in replies[0][0]


@pytest.mark.asyncio
async def test_chat_flow(db_session):
    from database.models import AdChatMessage

    _r, state, _ = await dialog.handle(
        db_session, _btn("chat"), None, submit=_Submit(), now_msk=NOW
    )
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
    replies, state, _ = await dialog.handle(
        db_session, _msg("Продам дрова"), state, submit=s, now_msk=NOW
    )
    assert state["step"] == "order_regions" and "Арбаж" in replies[0][1]  # кнопка района
    _r, state, _ = await dialog.handle(db_session, _msg("1, 3"), state, submit=s, now_msk=NOW)
    assert state["step"] == "order_when" and state["draft"]["region_ids"] == [r1.id, r2.id]
    replies, state, _ = await dialog.handle(
        db_session, _msg("25.09 14:30"), state, submit=s, now_msk=NOW
    )
    assert state["step"] == "order_confirm" and "районов: 2" in replies[0][0]
    replies, state, events = await dialog.handle(
        db_session, _btn("confirm"), state, submit=s, now_msk=NOW
    )

    assert state is None and "order" in events
    assert s.calls and s.calls[0][1]["text"] == "Продам дрова"
    assert (
        s.calls[0][1]["publish_at"] == "2026-09-25T14:30:00"
        and s.calls[0][1]["publish_now"] is False
    )
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
    _r, state, _ = await dialog.handle(
        db_session, _btn("all_regions"), state, submit=s, now_msk=NOW
    )
    assert len(state["draft"]["region_ids"]) == 2
    _r, state, _ = await dialog.handle(db_session, _btn("now"), state, submit=s, now_msk=NOW)
    assert state["draft"]["publish_now"] is True
    await dialog.handle(db_session, _btn("confirm"), state, submit=s, now_msk=NOW)
    assert s.calls[0][1]["publish_now"] is True


@pytest.mark.asyncio
async def test_order_error_is_reported_and_state_reset(db_session):
    await _region(db_session, "А", -1)
    s = _Submit(error=client_orders.OrderError("Пакет исчерпан"))
    state = {
        "step": "order_confirm",
        "draft": {"text": "t", "region_ids": [1], "publish_now": True},
    }
    replies, state, events = await dialog.handle(
        db_session, _btn("confirm"), state, submit=s, now_msk=NOW
    )
    assert state is None and "Пакет исчерпан" in replies[0][0] and "order" not in events
    kinds = [r.kind for r in (await db_session.execute(select(AdInteraction))).scalars().all()]
    assert "cabinet_order_refused" in kinds


@pytest.mark.asyncio
async def test_cancel_resets_from_any_step(db_session):
    replies, state, _ = await dialog.handle(
        db_session,
        _msg("отмена"),
        {"step": "order_when", "draft": {"x": 1}},
        submit=_Submit(),
        now_msk=NOW,
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
                {
                    "type": "message_new",
                    "object": {
                        "message": {"from_id": 900, "text": "", "payload": '{"cmd":"chat"}'}
                    },
                },
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
    import tasks.vk_bot_tasks  # noqa: F401
    from tasks.celery_app import app

    assert "tasks.vk_bot_tasks.poll_sarafan_vk_bot" in app.tasks
    # Long Poll крутит демон setka-vk-bot; в beat тика быть не должно —
    # два читателя одного Long Poll делят события между собой.
    assert "sarafan-vk-bot" not in app.conf.beat_schedule


def test_region_label_and_keyboard_pages():
    assert dialog.region_label("КИРОВО-ЧЕПЕЦК - ИНФО") == "Кирово-че"
    assert dialog.region_label("Уни - ИНФО") == "Уни"
    regions = [(i, f"Район{i:02d} - ИНФО") for i in range(1, 44)]  # 43, как на проде
    kb0 = json.loads(dialog.regions_keyboard(regions, [2], 0))
    kb1 = json.loads(dialog.regions_keyboard(regions, [2], 1))
    for kb in (kb0, kb1):
        assert len(kb["buttons"]) <= 10 and all(len(r) <= 5 for r in kb["buttons"])
        assert sum(len(r) for r in kb["buttons"]) <= 40
    labels0 = [b["action"]["label"] for r in kb0["buttons"] for b in r]
    assert "✅ Район02" in labels0 and any(x.startswith("Ещё ▶") for x in labels0)
    labels1 = [b["action"]["label"] for r in kb1["buttons"] for b in r]
    assert any(x.startswith("◀ Ещё") for x in labels1) and "✅ Готово" in labels1


@pytest.mark.asyncio
async def test_regions_by_buttons_toggle_page_done(db_session):
    r1 = await _region(db_session, "Арбаж", -1)
    r2 = await _region(db_session, "Уржум", -2)
    s = _Submit()
    _r, state, _ = await dialog.handle(db_session, _btn("order"), None, submit=s, now_msk=NOW)
    replies, state, _ = await dialog.handle(db_session, _msg("текст"), state, submit=s, now_msk=NOW)
    assert state["step"] == "order_regions" and replies[0][1] is not None
    tap = lambda rid: dialog.Incoming(peer_id=500, payload={"cmd": "rg", "id": rid})  # noqa: E731
    replies, state, _ = await dialog.handle(db_session, tap(r1.id), state, submit=s, now_msk=NOW)
    assert state["draft"]["region_ids"] == [r1.id] and "Выбрано 1" in replies[0][0]
    replies, state, _ = await dialog.handle(db_session, tap(r2.id), state, submit=s, now_msk=NOW)
    assert state["draft"]["region_ids"] == [r1.id, r2.id]
    replies, state, _ = await dialog.handle(db_session, tap(r1.id), state, submit=s, now_msk=NOW)
    assert state["draft"]["region_ids"] == [r2.id]  # повторное нажатие снимает
    replies, state, _ = await dialog.handle(
        db_session, _btn("rgdone"), state, submit=s, now_msk=NOW
    )
    assert state["step"] == "order_when" and state["draft"]["region_ids"] == [r2.id]


@pytest.mark.asyncio
async def test_regions_done_with_nothing_chosen_stays(db_session):
    await _region(db_session, "А", -1)
    s = _Submit()
    _r, state, _ = await dialog.handle(db_session, _btn("order"), None, submit=s, now_msk=NOW)
    _r, state, _ = await dialog.handle(db_session, _msg("текст"), state, submit=s, now_msk=NOW)
    replies, state, _ = await dialog.handle(
        db_session, _btn("rgdone"), state, submit=s, now_msk=NOW
    )
    assert state["step"] == "order_regions" and "ни один район" in replies[0][0]


# ───────── «Я оплатил» и свободный текст (PR 1.7) ─────────


async def _awaiting(session, client, amount):
    from decimal import Decimal

    from database.models import AdPayment

    p = AdPayment(client_id=client.id, amount=Decimal(str(amount)), status="awaiting")
    session.add(p)
    await session.flush()
    return p


@pytest.mark.asyncio
async def test_paid_button_claims_awaiting_and_emits_event(db_session):
    from database.models import AdPayment

    # первый контакт заводит карточку
    await dialog.handle(db_session, _btn("balance"), None, submit=_Submit(), now_msk=NOW)
    client = await dialog.find_client(db_session, 500)
    pay = await _awaiting(db_session, client, 350)

    replies, state, events = await dialog.handle(
        db_session, _btn("paid"), None, submit=_Submit(), now_msk=NOW
    )
    assert state is None and "payment_claimed" in events
    assert "350" in replies[0][0]
    await db_session.flush()
    assert pay.claimed_at is not None
    assert (await db_session.execute(select(AdPayment))).scalars().one().status == "awaiting"

    replies, _s, events = await dialog.handle(
        db_session, _msg("✅ Оплатил"), None, submit=_Submit(), now_msk=NOW
    )
    assert "payment_claimed" not in events and "нет" in replies[0][0].lower()


@pytest.mark.asyncio
async def test_free_text_outside_step_goes_to_chat_for_known_client(db_session):
    from database.models import AdChatMessage

    # Новый клиент: первое сообщение — приветствие, не письмо владельцу.
    replies, state, events = await dialog.handle(
        db_session, _msg("привет"), None, submit=_Submit(), now_msk=NOW
    )
    assert "signup" in events and "chat" not in events
    assert (await db_session.execute(select(AdChatMessage))).scalars().all() == []

    # Тот же клиент вне шага пишет текст — это сообщение владельцу.
    replies, state, events = await dialog.handle(
        db_session, _msg("Я перевёл 700, проверьте"), None, submit=_Submit(), now_msk=NOW
    )
    assert state is None and events == ["chat"]
    assert "Передал владельцу" in replies[0][0]
    msg = (await db_session.execute(select(AdChatMessage))).scalar_one()
    assert msg.sender == "client" and "700" in msg.body


# ───────── фото (Этап 5) ─────────

PHOTO_ATT = {
    "type": "photo",
    "photo": {
        "sizes": [
            {"type": "m", "width": 130, "url": "u-small"},
            {"type": "x", "width": 604, "url": "u-big"},
        ]
    },
}


def test_extract_incoming_keeps_photo_attachments():
    inc = intake.extract_incoming(
        {
            "type": "message_new",
            "object": {"message": {"from_id": 7, "text": "", "attachments": [PHOTO_ATT, "мусор"]}},
        }
    )
    assert len(inc.attachments) == 1 and inc.attachments[0]["type"] == "photo"


@pytest.fixture
def photo_store(monkeypatch, tmp_path):
    monkeypatch.setenv("AD_UPLOAD_DIR", str(tmp_path))
    monkeypatch.setenv("AD_UPLOAD_MIN_FREE_BYTES", "0")
    return tmp_path


def _fetch_ok(seen):
    async def fetch(url):
        seen.append(url)
        return b"\xff\xd8\xff"

    return fetch


def _photo_msg(text="", n=1):
    return dialog.Incoming(peer_id=500, text=text, attachments=[PHOTO_ATT] * n)


@pytest.mark.asyncio
async def test_order_with_photos_reaches_submit(db_session, photo_store):
    await _region(db_session, "Арбаж", -1)
    await _region(db_session, "Уржум", -2)
    s = _Submit()
    seen = []
    fetch = _fetch_ok(seen)
    kw = dict(submit=s, now_msk=NOW, photo_fetch=fetch)
    _r, state, _ = await dialog.handle(db_session, _btn("order"), None, submit=s, now_msk=NOW)
    # текст + фото одним сообщением
    replies, state, _ = await dialog.handle(db_session, _photo_msg("Продам дрова"), state, **kw)
    assert seen == ["u-big"] and state["step"] == "order_regions"
    assert len(state["draft"]["photos"]) == 1 and "Фото добавлено: 1" in replies[0][0]
    client = (await db_session.execute(select(AdClient))).scalar_one()
    assert (photo_store / str(client.id) / state["draft"]["photos"][0]).exists()
    # фото отдельным сообщением на шаге районов — остаёмся на шаге, фото копятся
    replies, state, _ = await dialog.handle(db_session, _photo_msg(), state, **kw)
    assert state["step"] == "order_regions" and len(state["draft"]["photos"]) == 2
    assert "всего 2" in replies[0][0] and "Арбаж" in replies[0][1]
    _r, state, _ = await dialog.handle(db_session, _msg("1, 2"), state, **kw)
    replies, state, _ = await dialog.handle(db_session, _btn("now"), state, **kw)
    assert state["step"] == "order_confirm" and "фото: 2" in replies[0][0]
    _r, state, events = await dialog.handle(db_session, _btn("confirm"), state, **kw)
    assert state is None and "order" in events
    photos = s.calls[0][1]["photos"]
    assert len(photos) == 2 and all(p.endswith(".jpg") for p in photos)
    assert intake.order_kwargs(s.calls[0][1])["image_paths"] == photos


@pytest.mark.asyncio
async def test_photo_only_post_via_done(db_session, photo_store):
    await _region(db_session, "А", -1)
    s = _Submit()
    kw = dict(submit=s, now_msk=NOW, photo_fetch=_fetch_ok([]))
    _r, state, _ = await dialog.handle(db_session, _btn("order"), None, submit=s, now_msk=NOW)
    replies, state, _ = await dialog.handle(db_session, _photo_msg(), state, **kw)
    assert state["step"] == "order_text" and "Готово" in replies[0][0]
    assert replies[0][1] == dialog.TEXT_DONE_KEYBOARD
    _r, state, _ = await dialog.handle(db_session, _btn("rgdone"), state, **kw)
    assert state["step"] == "order_regions" and state["draft"]["text"] == ""
    # без фото и без текста — как раньше
    _r, st2, _ = await dialog.handle(db_session, _btn("order"), None, submit=s, now_msk=NOW)
    replies, st2, _ = await dialog.handle(db_session, _msg(""), st2, **kw)
    assert st2["step"] == "order_text" and "Текст пустой" in replies[0][0]
    replies, st2, _ = await dialog.handle(db_session, _btn("rgdone"), st2, **kw)
    assert st2["step"] == "order_text" and "Текст пустой" in replies[0][0]


@pytest.mark.asyncio
async def test_photo_errors_do_not_break_dialog(db_session, photo_store):
    s = _Submit()
    state = {"step": "order_text", "draft": {}}

    async def bad_fetch(url):
        return None

    replies, state, _ = await dialog.handle(
        db_session, _photo_msg(), state, submit=s, now_msk=NOW, photo_fetch=bad_fetch
    )
    assert state["step"] == "order_text" and "не удалось скачать" in replies[0][0].lower()
    assert state["draft"].get("photos", []) == []
    # лимит библиотеки: 20 файлов лежат и все заняты активными постами (вытеснять
    # нечего) — текст лимита, шаг сохранён, исключений нет
    from database.models import AdScheduledPost

    client = (await db_session.execute(select(AdClient))).scalar_one()
    d = photo_store / str(client.id)
    d.mkdir(exist_ok=True)
    names = [f"{i:02d}.jpg" for i in range(20)]
    for n in names:
        (d / n).write_bytes(b"x")
    db_session.add(
        AdScheduledPost(
            client_id=client.id,
            community_vk_id=-1,
            text="t",
            publish_date=NOW,
            status="pending",
            image_names=names,
        )
    )
    await db_session.flush()
    replies, state, _ = await dialog.handle(
        db_session, _photo_msg(), state, submit=s, now_msk=NOW, photo_fetch=_fetch_ok([])
    )
    assert state["step"] == "order_text" and "Лимит 20" in replies[0][0]
    # без photo_fetch вложения игнорируются — поведение прежнее
    replies, state, _ = await dialog.handle(db_session, _photo_msg(), state, submit=s, now_msk=NOW)
    assert "Текст пустой" in replies[0][0]


@pytest.mark.asyncio
async def test_cancel_removes_draft_photos(db_session, photo_store):
    s = _Submit()
    _r, state, _ = await dialog.handle(db_session, _btn("order"), None, submit=s, now_msk=NOW)
    _r, state, _ = await dialog.handle(
        db_session, _photo_msg(), state, submit=s, now_msk=NOW, photo_fetch=_fetch_ok([])
    )
    client = (await db_session.execute(select(AdClient))).scalar_one()
    path = photo_store / str(client.id) / state["draft"]["photos"][0]
    assert path.exists()
    replies, state, _ = await dialog.handle(
        db_session, _btn("cancel"), state, submit=s, now_msk=NOW
    )
    assert state is None and not path.exists()


@pytest.mark.asyncio
async def test_confirm_texts_for_photos_and_failed_posts(db_session):
    from types import SimpleNamespace

    await _region(db_session, "А", -1)
    st = {
        "step": "order_confirm",
        "draft": {"text": "t", "region_ids": [1], "publish_now": True, "photos": ["a.jpg"]},
    }
    s = _Submit(error=client_orders.OrderError("Пакет исчерпан"))
    replies, _state, _ = await dialog.handle(db_session, _btn("confirm"), st, submit=s, now_msk=NOW)
    assert "Фото остались" in replies[0][0]
    failed = SimpleNamespace(status="failed", error_message="нет user-токена")
    s = _Submit(result={"order_ref": "r", "price_total": 0, "posts": [failed], "moderation": False})
    replies, _state, events = await dialog.handle(
        db_session, _btn("confirm"), st, submit=s, now_msk=NOW
    )
    assert "ВК не принял" in replies[0][0] and "нет user-токена" in replies[0][0]
    assert "order_direct" in events
    s = _Submit(
        result={
            "order_ref": "r",
            "price_total": 350,
            "posts": [failed, SimpleNamespace(status="scheduled")],
            "moderation": False,
        }
    )
    replies, _state, _ = await dialog.handle(db_session, _btn("confirm"), st, submit=s, now_msk=NOW)
    assert "1 постов в очереди" in replies[0][0] and "1 ВК не принял" in replies[0][0]


@pytest.mark.asyncio
async def test_photo_outside_order_gets_hint_not_chat(db_session):
    s = _Submit()
    await dialog.handle(db_session, _msg("привет"), None, submit=s, now_msk=NOW)  # карточка есть
    replies, state, events = await dialog.handle(
        db_session, _photo_msg(), None, submit=s, now_msk=NOW, photo_fetch=_fetch_ok([])
    )
    assert state is None and "Заказать пост" in replies[0][0] and "chat" not in events


@pytest.mark.asyncio
async def test_handle_one_passes_photo_fetch(db_session, photo_store, monkeypatch):
    async def fake_owner(text, **kw):
        return {}

    monkeypatch.setattr("modules.ad_cabinet.vk_bot.notify.notify_owner", fake_owner)
    seen, sent = [], []
    states = {500: {"step": "order_text", "draft": {}}}

    async def sender(peer_id, text, kb=None):
        sent.append(text)
        return {"response": 1}

    class _Factory:
        def __call__(self):
            return self

        async def __aenter__(self):
            return db_session

        async def __aexit__(self, *a):
            return False

    await intake.handle_one(
        _photo_msg(),
        session_factory=_Factory(),
        state_get=states.get,
        state_set=lambda p, s: states.__setitem__(p, s),
        submit=_Submit(),
        name_fetch=None,
        sender=sender,
        photo_fetch=_fetch_ok(seen),
    )
    assert seen == ["u-big"] and states[500]["step"] == "order_text"
    assert len(states[500]["draft"]["photos"]) == 1 and "Фото добавлено" in sent[0]


# ───────── фото: находки ревью PR-1 ─────────


@pytest.mark.asyncio
@pytest.mark.parametrize("step", ["order_regions", "order_when", "order_confirm"])
async def test_photo_with_caption_on_later_steps_keeps_note(db_session, photo_store, step):
    """Фото с подписью на шагах районов/даты/подтверждения: файл лёг, заметка в ответе."""
    r = await _region(db_session, "А", -1)
    s = _Submit()
    draft = {"text": "t", "regions": [[r.id, "А"]], "region_ids": [r.id], "page": 0}
    if step == "order_confirm":
        draft["publish_now"] = True
    caption = {"order_regions": "1", "order_when": "25.09 14:30", "order_confirm": "ок"}[step]
    replies, state, _ = await dialog.handle(
        db_session,
        _photo_msg(caption),
        {"step": step, "draft": draft},
        submit=s,
        now_msk=NOW,
        photo_fetch=_fetch_ok([]),
    )
    assert "Фото добавлено: 1" in replies[0][0]
    assert len(state["draft"]["photos"]) == 1
    expected_next = {
        "order_regions": "order_when",
        "order_when": "order_confirm",
        "order_confirm": "order_confirm",
    }[step]
    assert state["step"] == expected_next
    # неудачная закачка с подписью — предупреждение не теряется
    replies, state, _ = await dialog.handle(
        db_session,
        _photo_msg("ерунда"),
        {"step": "order_when", "draft": dict(draft)},
        submit=s,
        now_msk=NOW,
        photo_fetch=_none_fetch,
    )
    assert "не удалось скачать" in replies[0][0].lower() and "Не понял дату" in replies[0][0]


async def _none_fetch(url):
    return None


@pytest.mark.asyncio
async def test_collect_photos_never_raises(db_session, photo_store, monkeypatch):
    from modules.ad_cabinet import client_photos

    s = _Submit()
    base = {"step": "order_text", "draft": {}}

    async def boom(url):
        raise RuntimeError("net")

    replies, state, _ = await dialog.handle(
        db_session, _photo_msg(), base, submit=s, now_msk=NOW, photo_fetch=boom
    )
    assert state["step"] == "order_text" and "не удалось скачать" in replies[0][0].lower()

    def store_boom(*a, **k):
        raise PermissionError("ro")

    monkeypatch.setattr(client_photos, "store_client_photo", store_boom)
    replies, state, _ = await dialog.handle(
        db_session, _photo_msg(), base, submit=s, now_msk=NOW, photo_fetch=_fetch_ok([])
    )
    assert state["step"] == "order_text" and "не удалось сохранить" in replies[0][0]
    monkeypatch.undo()

    full = {"step": "order_text", "draft": {"photos": [f"{i}.jpg" for i in range(10)]}}
    replies, state, _ = await dialog.handle(
        db_session, _photo_msg(), full, submit=s, now_msk=NOW, photo_fetch=_fetch_ok([])
    )
    assert "уже 10 фото" in replies[0][0] and len(state["draft"]["photos"]) == 10

    nine = {"step": "order_text", "draft": {"photos": [f"{i}.jpg" for i in range(9)]}}
    replies, state, _ = await dialog.handle(
        db_session, _photo_msg(n=2), nine, submit=s, now_msk=NOW, photo_fetch=_fetch_ok([])
    )
    assert "Фото добавлено: 1" in replies[0][0] and "Лишние не взял" in replies[0][0]
    assert len(state["draft"]["photos"]) == 10


@pytest.mark.asyncio
async def test_cancel_keeps_photo_used_by_cabinet_post(db_session, photo_store):
    """Файл черновика, уже выбранный в активный пост из кабинета, отмена не удаляет."""
    from database.models import AdScheduledPost

    s = _Submit()
    _r, state, _ = await dialog.handle(db_session, _btn("order"), None, submit=s, now_msk=NOW)
    _r, state, _ = await dialog.handle(
        db_session, _photo_msg(n=2), state, submit=s, now_msk=NOW, photo_fetch=_fetch_ok([])
    )
    client = (await db_session.execute(select(AdClient))).scalar_one()
    used, loose = state["draft"]["photos"]
    db_session.add(
        AdScheduledPost(
            client_id=client.id,
            community_vk_id=-1,
            text="из кабинета",
            publish_date=NOW,
            status="pending",
            image_names=[used],
        )
    )
    await db_session.flush()
    _r, state, _ = await dialog.handle(db_session, _btn("cancel"), state, submit=s, now_msk=NOW)
    d = photo_store / str(client.id)
    assert (d / used).exists() and not (d / loose).exists()


@pytest.mark.asyncio
async def test_full_library_evicts_oldest_unreferenced(db_session, photo_store):
    """Клиент только из бота: 20 файлов от вышедших постов — самый старый вытесняется."""
    import os
    import time

    from database.models import AdScheduledPost

    s = _Submit()
    _r, state, _ = await dialog.handle(db_session, _btn("order"), None, submit=s, now_msk=NOW)
    client = (await db_session.execute(select(AdClient))).scalar_one()
    d = photo_store / str(client.id)
    d.mkdir(exist_ok=True)
    base = time.time() - 10_000
    for i in range(20):
        p = d / f"{i:02d}.jpg"
        p.write_bytes(b"x")
        os.utime(p, (base + i, base + i))
    # 00.jpg — самый старый, но занят активным постом: вытесняется 01.jpg
    db_session.add(
        AdScheduledPost(
            client_id=client.id,
            community_vk_id=-1,
            text="t",
            publish_date=NOW,
            status="scheduled",
            image_names=["00.jpg"],
        )
    )
    await db_session.flush()
    replies, state, _ = await dialog.handle(
        db_session, _photo_msg(), state, submit=s, now_msk=NOW, photo_fetch=_fetch_ok([])
    )
    assert "Фото добавлено: 1" in replies[0][0] and len(state["draft"]["photos"]) == 1
    names = {p.name for p in d.iterdir()}
    assert "00.jpg" in names and "01.jpg" not in names and len(names) == 20
