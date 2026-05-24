"""Tests for utils.vk_url.parse_vk_group_url."""

from __future__ import annotations

from utils.vk_url import parse_vk_group_url


def test_club_url():
    assert parse_vk_group_url("https://vk.com/club12345") == (12345, None)


def test_public_url():
    assert parse_vk_group_url("https://vk.com/public98765") == (98765, None)


def test_screen_name_url():
    assert parse_vk_group_url("https://vk.com/setka_test") == (None, "setka_test")


def test_url_without_scheme():
    assert parse_vk_group_url("vk.com/club42") == (42, None)


def test_url_with_mobile_host():
    assert parse_vk_group_url("https://m.vk.com/club777") == (777, None)


def test_url_with_query_string():
    assert parse_vk_group_url("https://vk.com/club555?w=wall-555") == (555, None)


def test_url_with_trailing_path():
    assert parse_vk_group_url("https://vk.com/myclub/photos") == (None, "myclub")


def test_numeric_id():
    assert parse_vk_group_url("12345") == (12345, None)


def test_negative_numeric_id():
    """Минус — частая привычка вставлять `Community.vk_id`; принимаем."""
    assert parse_vk_group_url("-12345") == (12345, None)


def test_empty_input():
    assert parse_vk_group_url("") == (None, None)
    assert parse_vk_group_url("   ") == (None, None)


def test_garbage():
    assert parse_vk_group_url("not a url at all !!!") == (None, None)


def test_screen_name_lowercased():
    assert parse_vk_group_url("https://vk.com/MyClub") == (None, "myclub")


def test_screen_name_too_short_rejected():
    # VK screen_name minimum is 5 chars in practice; regex requires ≥3.
    assert parse_vk_group_url("https://vk.com/ab") == (None, None)


def test_zero_id_rejected():
    assert parse_vk_group_url("0") == (None, None)
    assert parse_vk_group_url("https://vk.com/club0") == (None, None)


def test_www_prefix():
    assert parse_vk_group_url("https://www.vk.com/club42") == (42, None)


def test_vk_ru_domain():
    assert parse_vk_group_url("https://vk.ru/club42") == (42, None)
