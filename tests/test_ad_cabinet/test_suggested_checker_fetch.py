"""Тесты полного fetch предложки + парсинга автора (R1)."""

from __future__ import annotations

from modules.notifications.vk_suggested_checker import VKSuggestedChecker, _extract_photo_urls


def _checker() -> VKSuggestedChecker:
    return VKSuggestedChecker("fake-token")


def test_parse_user_author():
    item = {"id": 10, "from_id": 42, "text": "hi", "attachments": []}
    profiles = {42: {"id": 42, "first_name": "Иван", "last_name": "Петров"}}
    out = VKSuggestedChecker.parse_suggested_item(item, profiles, {}, -100)
    assert out["peer_id"] == 42
    assert out["author_is_group"] is False
    assert out["author_name"] == "Иван Петров"
    assert out["community_vk_id"] == -100


def test_parse_signer_preferred_over_group_from_id():
    # from_id — это сама группа, человек в signer_id (R1).
    item = {"id": 11, "from_id": -100, "signer_id": 55, "text": "x", "attachments": []}
    profiles = {55: {"id": 55, "first_name": "Мария", "last_name": "С"}}
    out = VKSuggestedChecker.parse_suggested_item(item, profiles, {}, -100)
    assert out["peer_id"] == 55
    assert out["author_is_group"] is False
    assert out["author_name"] == "Мария С"


def test_parse_group_author():
    item = {"id": 12, "from_id": -200, "text": "x", "attachments": []}
    groups = {200: {"id": 200, "name": "Рога и Копыта"}}
    out = VKSuggestedChecker.parse_suggested_item(item, {}, groups, -100)
    assert out["author_is_group"] is True
    assert out["author_name"] == "Рога и Копыта"
    assert out["peer_id"] == -200


def test_extract_photo_urls_picks_largest():
    att = [
        {
            "type": "photo",
            "photo": {
                "sizes": [
                    {"url": "small", "width": 100},
                    {"url": "big", "width": 800},
                ]
            },
        }
    ]
    assert _extract_photo_urls(att) == ["big"]


def test_fetch_suggested_posts_parses(monkeypatch):
    checker = _checker()
    payload = {
        "count": 1,
        "items": [{"id": 10, "from_id": 42, "text": "Размещу рекламу", "attachments": []}],
        "profiles": [{"id": 42, "first_name": "Иван", "last_name": "Петров"}],
        "groups": [],
    }
    monkeypatch.setattr(checker, "_call_with_fallback", lambda *a, **k: (payload, "user-token"))
    out = checker.fetch_suggested_posts(-100)
    assert len(out) == 1
    assert out[0]["author_name"] == "Иван Петров"
    assert out[0]["vk_post_id"] == 10
