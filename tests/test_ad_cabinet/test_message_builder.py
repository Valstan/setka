"""Тесты рендера ответа-оффера."""

from __future__ import annotations

from modules.ad_cabinet.message_builder import (
    VK_MESSAGE_MAX_LEN,
    _format_number,
    looks_mangled,
    render,
)

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


def test_communities_count_placeholder():
    out = render("Сеть: {communities_count} групп.", communities_count=26)
    assert "Сеть: 26 групп." == out


def test_subscribers_count_placeholder():
    out = render("Подписчиков: {subscribers_count}.", subscribers_count=29556)
    assert "Подписчиков: 29 556." == out


def test_stats_placeholders_empty_when_none():
    out = render(
        "{communities_count} {subscribers_count}", communities_count=None, subscribers_count=None
    )
    assert out.strip() == ""


def test_looks_mangled_detects_lost_cyrillic():
    # Ровно то, что ушло рекламодателям 2026-08-07 из испорченного env.
    assert looks_mangled("????????????, {author_name}! ??????? ?? ???? ???????????")


def test_looks_mangled_ignores_normal_text():
    assert not looks_mangled(TPL)
    assert not looks_mangled("Готовы разместить? Пишите!")
    assert not looks_mangled("Что?? Уже?")  # две подряд — ещё не поломка
    assert not looks_mangled("")
    assert not looks_mangled(None)


def test_format_number():
    assert _format_number(0) == "0"
    assert _format_number(100) == "100"
    assert _format_number(1000) == "1 000"
    assert _format_number(29556) == "29 556"
    assert _format_number(1234567) == "1 234 567"
