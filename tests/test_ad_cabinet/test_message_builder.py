"""Тесты рендера ответа-оффера."""

from __future__ import annotations

from modules.ad_cabinet.message_builder import VK_MESSAGE_MAX_LEN, render

TPL = (
    "Здравствуйте, {author_name}! Спасибо за пост в «{community_name}». "
    "Размещение рекламы — по прайсу."
)


def test_substitutes_placeholders():
    out = render(TPL, author_name="Иван", community_name="Малмыж Инфо", region_name="Малмыж")
    assert "Иван" in out
    assert "Малмыж Инфо" in out
    assert "{author_name}" not in out
    assert "{community_name}" not in out


def test_missing_name_no_dangling_comma():
    out = render(TPL, author_name=None, community_name="Малмыж Инфо")
    assert "Здравствуйте!" in out
    assert ", !" not in out


def test_unknown_placeholder_is_safe():
    out = render("Привет {unknown}!", author_name="Иван")
    assert "{unknown}" not in out


def test_truncates_to_vk_limit():
    out = render("x" * 5000)
    assert len(out) <= VK_MESSAGE_MAX_LEN
