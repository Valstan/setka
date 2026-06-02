"""Тесты ORM-модели AdRequest (миграция 021)."""

from __future__ import annotations

from database.models import AdRequest


def test_tablename():
    assert AdRequest.__tablename__ == "ad_requests"


def test_to_dict_includes_fields_and_urls():
    ar = AdRequest(
        id=5,
        region_id=7,
        community_vk_id=-158787639,
        community_name="Малмыж Инфо",
        vk_post_id=12345,
        author_vk_id=42,
        signer_id=42,
        peer_id=42,
        author_name="Иван Петров",
        author_is_group=False,
        text_snapshot="Размещу рекламу",
        score=5,
        reasons_json=["слово «реклама»"],
        status="new",
    )
    d = ar.to_dict()
    assert d["id"] == 5
    assert d["status"] == "new"
    assert d["score"] == 5
    assert d["reasons_json"] == ["слово «реклама»"]
    assert d["vk_post_url"] == "https://vk.com/wall-158787639_12345"
    assert d["author_url"] == "https://vk.com/id42"


def test_author_url_for_group():
    ar = AdRequest(
        id=1,
        community_vk_id=-1,
        vk_post_id=2,
        author_vk_id=-100,
        author_is_group=True,
    )
    assert ar.to_dict()["author_url"] == "https://vk.com/club100"
