"""Тесты расширения send_message: attachment + обработка 901."""

from __future__ import annotations

from unittest.mock import MagicMock

from vk_api.exceptions import ApiError

import modules.notifications.vk_actions as va


def _fake_vkapi(send_impl):
    api = MagicMock()
    api.messages.send.side_effect = send_impl
    holder = MagicMock()
    holder.get_api.return_value = api
    return holder, api


def _api_error(code: int) -> ApiError:
    e = ApiError.__new__(ApiError)
    e.code = code
    e.error = {"error_code": code, "error_msg": "test"}
    return e


def test_send_message_success_with_attachment(monkeypatch):
    captured = {}

    def send_impl(**kwargs):
        captured.update(kwargs)
        return 12345

    holder, _api = _fake_vkapi(send_impl)
    monkeypatch.setattr(va.vk_api, "VkApi", lambda token=None: holder)

    res = va.send_message(
        group_id=-100,
        peer_id=42,
        message="Здравствуйте",
        user_token="u",
        attachment="photo-5_99",
    )
    assert res["success"] is True
    assert res["message_id"] == 12345
    assert captured.get("attachment") == "photo-5_99"


def test_send_message_901_returns_deeplink(monkeypatch):
    def send_impl(**kwargs):
        raise _api_error(901)

    holder, _api = _fake_vkapi(send_impl)
    monkeypatch.setattr(va.vk_api, "VkApi", lambda token=None: holder)

    res = va.send_message(group_id=-100, peer_id=42, message="hi", user_token="u")
    assert res["success"] is False
    assert res["error_code"] == 901
    assert res["allowed"] is False
    assert res["personal_deeplink"] == "https://vk.com/im?sel=42"
