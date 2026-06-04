"""Тесты VKDialogsChecker.fetch_history (блок A, тред-вью): нормализация истории."""

from __future__ import annotations

from unittest.mock import MagicMock

from modules.notifications.vk_dialogs_checker import VKDialogsChecker


def _checker():
    # VkApi(token=...) не ходит в сеть до первого метода — конструктор безопасен.
    return VKDialogsChecker("faketoken")


def test_fetch_history_normalizes_and_reverses(monkeypatch):
    checker = _checker()
    fake_api = MagicMock()
    fake_api.messages.getHistory.return_value = {
        "items": [  # VK отдаёт новые→старые
            {"out": 1, "from_id": -100, "text": "наш ответ", "date": 200, "attachments": []},
            {
                "out": 0,
                "from_id": 42,
                "text": "размещу рекламу",
                "date": 100,
                "attachments": [{"type": "photo"}],
            },
        ],
        "profiles": [{"id": 42, "first_name": "Иван", "last_name": "Петров"}],
    }
    monkeypatch.setattr(checker, "_api_for", lambda gid: (fake_api, True))

    msgs = checker.fetch_history(-100, 42)
    assert len(msgs) == 2
    # развёрнуто: старое сообщение первым
    assert msgs[0]["text"] == "размещу рекламу"
    assert msgs[0]["out"] is False
    assert msgs[0]["from_name"] == "Иван Петров"
    assert msgs[0]["attachments"] == 1
    assert msgs[1]["out"] is True  # наш ответ — последним


def test_fetch_history_community_token_omits_group_id(monkeypatch):
    checker = _checker()
    fake_api = MagicMock()
    fake_api.messages.getHistory.return_value = {"items": [], "profiles": []}
    monkeypatch.setattr(checker, "_api_for", lambda gid: (fake_api, True))

    checker.fetch_history(-100, 42, count=10)
    kwargs = fake_api.messages.getHistory.call_args.kwargs
    assert "group_id" not in kwargs  # community-токену group_id не нужен
    assert kwargs["peer_id"] == 42
    assert kwargs["count"] == 10
    assert kwargs["extended"] == 1


def test_fetch_history_user_token_passes_group_id(monkeypatch):
    checker = _checker()
    fake_api = MagicMock()
    fake_api.messages.getHistory.return_value = {"items": [], "profiles": []}
    monkeypatch.setattr(checker, "_api_for", lambda gid: (fake_api, False))

    checker.fetch_history(-100, 42)
    kwargs = fake_api.messages.getHistory.call_args.kwargs
    assert kwargs["group_id"] == 100  # user-токену group_id обязателен (abs)


def test_fetch_history_caps_count(monkeypatch):
    checker = _checker()
    fake_api = MagicMock()
    fake_api.messages.getHistory.return_value = {"items": [], "profiles": []}
    monkeypatch.setattr(checker, "_api_for", lambda gid: (fake_api, True))

    checker.fetch_history(-100, 42, count=9999)
    assert fake_api.messages.getHistory.call_args.kwargs["count"] == 100
