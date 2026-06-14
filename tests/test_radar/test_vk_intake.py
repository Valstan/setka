"""Tests for modules/radar/vk_intake — VK Bots Long Poll → привязка VK-лички."""

import pytest

from modules.radar import vk_intake


def _msg(from_id, text):
    return {"type": "message_new", "object": {"message": {"from_id": from_id, "text": text}}}


# ───────────────────────── чистые функции ─────────────────────────


def test_extract_message_ok():
    assert vk_intake.extract_message(_msg(123, "ABC")) == (123, "ABC")


def test_extract_message_not_message_new():
    assert vk_intake.extract_message({"type": "message_edit", "object": {}}) == (None, "")


def test_extract_message_group_from_id_ignored():
    # from_id отрицательный (сообщество) — не привязываем.
    assert vk_intake.extract_message(_msg(-5, "x")) == (None, "")


def test_extract_code_plain_and_start():
    assert vk_intake._extract_code("ABC123") == "ABC123"
    assert vk_intake._extract_code("/start ABC123") == "ABC123"
    assert vk_intake._extract_code("ABC123 лишнее") == "ABC123"
    assert vk_intake._extract_code("") == ""


# ───────────────────────── handle_update ─────────────────────────


async def _link(status):
    async def _fn(code, vk_user_id, display_name):
        return {"status": status}

    return _fn


@pytest.mark.asyncio
async def test_handle_update_links():
    out = await vk_intake.handle_update(_msg(42, "CODE12"), link_account=await _link("linked"))
    assert out[0] == 42
    assert out[1].startswith("✅")


@pytest.mark.asyncio
async def test_handle_update_invalid_code():
    out = await vk_intake.handle_update(_msg(42, "BADD"), link_account=await _link("invalid"))
    assert out[1].startswith("❌")


@pytest.mark.asyncio
async def test_handle_update_no_code_silent():
    # Сообщение без текста-кода → молчим (None).
    out = await vk_intake.handle_update(_msg(42, "   "), link_account=await _link("linked"))
    assert out is None


# ───────────────────────── poll_vk_intake_once ─────────────────────────


@pytest.mark.asyncio
async def test_poll_processes_message_and_advances_ts():
    state = {"ts": None}
    replies = []

    def api_call(token, method, **params):
        assert method == "groups.getLongPollServer"
        return {"response": {"server": "https://lp", "key": "k", "ts": "5"}}

    def lp_get(server, key, ts, wait=10):
        assert ts == "5"  # стартуем со свежего ts (без бэклога)
        return {"ts": "6", "updates": [_msg(42, "CODE12")]}

    async def link_account(code, vk_user_id, display_name):
        return {"status": "linked"}

    async def reply(peer_id, text):
        replies.append((peer_id, text))

    res = await vk_intake.poll_vk_intake_once(
        token="t",
        group_id=137760500,
        link_account=link_account,
        reply=reply,
        ts_get=lambda: state["ts"],
        ts_set=lambda v: state.__setitem__("ts", v),
        api_call=api_call,
        lp_get=lp_get,
    )
    assert res["ok"] and res["linked"] == 1
    assert replies and replies[0][0] == 42 and replies[0][1].startswith("✅")
    assert state["ts"] == "6"


@pytest.mark.asyncio
async def test_poll_long_poll_server_error():
    def api_call(token, method, **params):
        return {"error": {"error_msg": "longpoll for this group is not enabled."}}

    res = await vk_intake.poll_vk_intake_once(
        token="t",
        group_id=1,
        link_account=lambda *a: None,
        reply=lambda *a: None,
        ts_get=lambda: None,
        ts_set=lambda v: None,
        api_call=api_call,
        lp_get=lambda *a, **k: {},
    )
    assert res["ok"] is False


@pytest.mark.asyncio
async def test_poll_failed_2_reinitializes_ts():
    state = {"ts": "9"}

    def api_call(token, method, **params):
        return {"response": {"server": "https://lp", "key": "k", "ts": "9"}}

    def lp_get(server, key, ts, wait=10):
        return {"failed": 2}  # ключ протух → переинициализация

    res = await vk_intake.poll_vk_intake_once(
        token="t",
        group_id=1,
        link_account=lambda *a: None,
        reply=lambda *a: None,
        ts_get=lambda: state["ts"],
        ts_set=lambda v: state.__setitem__("ts", v),
        api_call=api_call,
        lp_get=lp_get,
    )
    assert res["ok"] and res.get("reinit") == 2
    assert state["ts"] is None  # сброшен → следующий тик возьмёт свежий
