"""Ссылки vk.com/wall в тексте сводок."""

from utils.vk_wall_links import extract_wall_post_refs_from_text


def test_extract_https_wall_url():
    text = "Источник: https://vk.com/wall-123456_789"
    assert extract_wall_post_refs_from_text(text) == [(-123456, 789)]


def test_extract_mobile_url_and_wall_token():
    text = "см. https://m.vk.com/wall-12345_99 и wall-1_2"
    out = extract_wall_post_refs_from_text(text)
    assert (-12345, 99) in out
    assert (-1, 2) in out


def test_dedupe_order():
    text = "wall-10_1 https://vk.com/wall-10_1"
    assert extract_wall_post_refs_from_text(text) == [(-10, 1)]
