"""Тесты VKMessagesChecker.check_all_region_groups — проброс conversations.

Регресс на баг: ``check_unread_messages`` возвращал ``conversations`` (превью
диалогов с текстом последнего сообщения), но ``check_all_region_groups`` не
клал их в notification-dict → UI всегда показывал «N непрочитанных» без текста.
"""

from __future__ import annotations

import asyncio

from modules.notifications.vk_messages_checker import VKMessagesChecker


def _checker() -> VKMessagesChecker:
    # VkApi(token=...) не ходит в сеть на __init__ — инстанс безопасен в тестах.
    return VKMessagesChecker("dummy-token")


def _region(group_id: int = -100) -> dict:
    return {
        "region_id": 1,
        "region_name": "Тест ИНФО",
        "region_code": "ti",
        "vk_group_id": group_id,
    }


def test_conversations_passed_through_to_notification():
    checker = _checker()
    convs = [
        {
            "conversation": {"peer": {"id": 555, "type": "user"}},
            "last_message": {"text": "Здравствуйте, реклама"},
        }
    ]
    checker.check_unread_messages = lambda gid: {
        "has_unread": True,
        "unread_count": 1,
        "total_conversations": 3,
        "group_id": gid,
        "url": f"https://vk.com/gim{abs(gid)}",
        "conversations": convs,
    }

    out = asyncio.run(checker.check_all_region_groups([_region(-100)]))
    notes = out["notifications"]
    assert len(notes) == 1
    assert notes[0]["conversations"] == convs
    # Текст последнего сообщения доступен фронту:
    assert notes[0]["conversations"][0]["last_message"]["text"] == "Здравствуйте, реклама"


def test_no_conversations_key_degrades_to_empty_list():
    checker = _checker()
    checker.check_unread_messages = lambda gid: {
        "has_unread": True,
        "unread_count": 2,
        "total_conversations": 2,
        "group_id": gid,
        "url": f"https://vk.com/gim{abs(gid)}",
        # conversations намеренно отсутствует
    }
    out = asyncio.run(checker.check_all_region_groups([_region(-200)]))
    assert out["notifications"][0]["conversations"] == []


def test_denied_group_not_in_notifications():
    checker = _checker()
    checker.check_unread_messages = lambda gid: {
        "has_unread": False,
        "unread_count": 0,
        "total_conversations": 0,
        "group_id": gid,
        "error": "Access denied",
        "error_code": 15,
    }
    out = asyncio.run(checker.check_all_region_groups([_region(-300)]))
    assert out["notifications"] == []
    assert len(out["denied_groups"]) == 1
    assert out["denied_groups"][0]["error_code"] == 15
