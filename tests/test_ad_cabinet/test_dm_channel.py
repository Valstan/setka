"""Пауза DM-канала после VK 9/14 (Этап 3).

- 9 → сутки, 14 → 6 часов, прочие коды — не пауза;
- paused_until/pause на in-process слое без Redis; продление не укорачивает;
- note_error шлёт алёрт через инъекцию и возвращает конец паузы;
- send_message не стучит в VK, пока канал на паузе, а 9 в ответе ставит паузу;
- бот-отправитель тоже уважает паузу и учитывает 14.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from modules.ad_cabinet import dm_channel


@pytest.fixture(autouse=True)
def _no_redis(monkeypatch):
    monkeypatch.setattr(dm_channel, "_redis", lambda: None)
    dm_channel.reset_for_tests()
    yield
    dm_channel.reset_for_tests()


def test_pause_seconds_by_code():
    assert dm_channel.pause_seconds(9) == 24 * 3600
    assert dm_channel.pause_seconds(14) == 6 * 3600
    assert dm_channel.pause_seconds(901) == 0 and dm_channel.pause_seconds(None) == 0


def test_pause_and_expiry_on_local_layer():
    now = 1_800_000_000.0
    assert dm_channel.paused_until(158787639, now=now) is None
    until = dm_channel.pause(-158787639, 600, now=now)
    assert until == datetime.utcfromtimestamp(now + 600)
    assert dm_channel.paused_until(158787639, now=now + 599) == until
    assert dm_channel.paused_until(158787639, now=now + 601) is None
    # продление длинной паузой не укорачивается короткой
    dm_channel.pause(158787639, 3600, now=now)
    dm_channel.pause(158787639, 60, now=now)
    assert dm_channel.paused_until(158787639, now=now + 3000) is not None


def test_note_error_pauses_and_alerts_only_for_flood_codes():
    alerts = []
    now = 1_800_000_000.0
    assert dm_channel.note_error(1, 901, now=now, alert=alerts.append) is None
    until = dm_channel.note_error(1, 14, now=now, alert=alerts.append)
    assert until == datetime.utcfromtimestamp(now + 6 * 3600)
    assert len(alerts) == 1 and "VK 14" in alerts[0] and "паузе 6 ч" in alerts[0]
    assert dm_channel.paused_until(1, now=now + 100) == until


def test_send_message_respects_pause_and_records_flood(monkeypatch):
    from vk_api.exceptions import ApiError

    from modules.notifications import vk_actions

    now = 1_800_000_000.0
    calls = []

    class _Api:
        class messages:  # noqa: N801
            @staticmethod
            def send(**params):
                calls.append(params)
                raise ApiError(
                    None, "messages.send", params, False, {"error_code": 9, "error_msg": "Flood"}
                )

    class _VkApi:
        def __init__(self, token):
            self.token = token

        def get_api(self):
            return _Api()

    monkeypatch.setattr(vk_actions, "vk_api", type("M", (), {"VkApi": _VkApi}))
    throttled = []
    monkeypatch.setattr(vk_actions, "_throttle", lambda token, op: throttled.append((token, op)))
    monkeypatch.setattr(dm_channel.time, "time", lambda: now)

    res = vk_actions.send_message(
        group_id=100, peer_id=7, message="привет", user_token="U", community_tokens={100: "C"}
    )
    assert res["success"] is False and res["error_code"] == 9 and "paused_until" in res
    assert throttled == [("C", "messages.send")] and len(calls) == 1

    # Канал на паузе — второй вызов в VK не идёт вовсе.
    res2 = vk_actions.send_message(
        group_id=100, peer_id=8, message="ещё", user_token="U", community_tokens={100: "C"}
    )
    assert res2["success"] is False and res2.get("paused_until") and len(calls) == 1
    # Другое сообщество не затронуто (но упадёт в тот же фейковый VK → 9).
    assert dm_channel.paused_until(200, now=now) is None


@pytest.mark.asyncio
async def test_bot_sender_respects_pause_and_notes_captcha(monkeypatch):
    from modules.ad_cabinet.vk_bot import notify

    now = 1_800_000_000.0
    monkeypatch.setattr(dm_channel.time, "time", lambda: now)
    posted = []

    class _Resp:
        def __init__(self, data):
            self._d = data

        def json(self):
            return self._d

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, data=None):
            posted.append(data)
            return _Resp({"error": {"error_code": 14, "error_msg": "Captcha needed"}})

    monkeypatch.setattr(notify.httpx, "AsyncClient", _Client)
    import modules.vk_monitor.vk_client as vkc

    monkeypatch.setattr(vkc, "enforce_token_rate_limit", lambda token, method="": None)
    monkeypatch.setattr(
        dm_channel,
        "note_error",
        lambda cid, code, **kw: dm_channel.pause(cid, dm_channel.pause_seconds(code), now=now),
    )

    out = await notify.vk_send("T", 241234091, 500, "текст")
    assert out["error"]["error_code"] == 14 and len(posted) == 1
    assert dm_channel.paused_until(241234091, now=now + 10) is not None
    out2 = await notify.vk_send("T", 241234091, 500, "ещё")
    assert out2["error"]["error_code"] == 9 and len(posted) == 1  # не стучали


def test_dm_ops_do_not_fall_back_to_user_token(monkeypatch):
    """У user-токенов нет scope messages: повтор user-токеном на 15/27 не делается."""
    from vk_api.exceptions import ApiError

    from modules.notifications import vk_actions

    used = []

    class _Api:
        def __init__(self, token):
            self.token = token

        class messages:  # noqa: N801
            pass

    class _VkApi:
        def __init__(self, token):
            self.token = token

        def get_api(self):
            api = _Api(self.token)

            def send(**params):
                used.append(self.token)
                raise ApiError(
                    None, "messages.send", params, False, {"error_code": 15, "error_msg": "denied"}
                )

            api.messages = type("M", (), {"send": staticmethod(send)})
            return api

    monkeypatch.setattr(vk_actions, "vk_api", type("M", (), {"VkApi": _VkApi}))
    monkeypatch.setattr(vk_actions, "_throttle", lambda token, op: None)
    res = vk_actions.send_message(
        group_id=100, peer_id=7, message="x", user_token="U", community_tokens={100: "C"}
    )
    assert res["success"] is False and res["error_code"] == 15
    assert used == ["C"]  # без повтора user-токеном
    # А для стеновых действий каскад 15/27 → user-токен сохранён.
    called = []

    def fn(api):
        called.append(api.token)
        if api.token == "C":
            raise ApiError(None, "likes.add", {}, False, {"error_code": 27, "error_msg": "group"})
        return {"ok": 1}

    resp, via = vk_actions._call_with_fallback(
        owner_id=-100, op_name="likes.add", fn=fn, user_token="U", community_tokens={100: "C"}
    )
    assert resp == {"ok": 1} and via == "community-fallback-user" and called == ["C", "U"]


@pytest.mark.asyncio
async def test_load_community_routing_ignores_user_tokens(monkeypatch):
    """Карта community-токенов не зависит от живости user-токенов."""
    import modules.vk_token_router as router

    async def fake_load(session):
        return {241234091: "sarafan-token"}

    class _S:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(router, "load_community_tokens", fake_load)
    import database.connection as conn

    monkeypatch.setattr(conn, "AsyncSessionLocal", lambda: _S())
    assert await router.load_community_routing() == {241234091: "sarafan-token"}


def test_flood_codes_never_disable_tokens():
    """9/14 — пауза канала, не автоотключение токена и не ротация публикатора."""
    import modules.vk_token_router as router

    assert not set(dm_channel.PAUSE_CODES) & set(router._AUTO_DISABLE_CODES_HOURS)
