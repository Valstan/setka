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


def test_inbound_dm_to_dict_origin_and_dialog_url():
    """ЛС-заявка (блок A): origin, last_message_id, dialog_url; vk_post_url=None."""
    ar = AdRequest(
        id=9,
        origin="inbound_dm",
        community_vk_id=-158787639,
        community_name="Малмыж Инфо",
        vk_post_id=None,
        last_message_id=777,
        author_vk_id=42,
        peer_id=42,
        author_name="Иван",
        author_is_group=False,
        text_snapshot="прайс на рекламу",
        status="new",
    )
    d = ar.to_dict()
    assert d["origin"] == "inbound_dm"
    assert d["last_message_id"] == 777
    assert d["vk_post_url"] is None
    assert d["dialog_url"] == "https://vk.com/gim158787639?sel=42"
    assert d["author_url"] == "https://vk.com/id42"


def test_suggested_to_dict_has_no_dialog_url():
    ar = AdRequest(id=1, community_vk_id=-1, vk_post_id=2, peer_id=5)
    # origin не задан явно → дефолт колонки применяется в БД, в py-объекте None.
    d = ar.to_dict()
    assert d["dialog_url"] is None
