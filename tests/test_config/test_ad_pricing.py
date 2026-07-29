"""Тесты config/ad_landing.py — ценовая лесенка заказа на лендинге.

Правило владельца (2026-07-29): цена набегает поштучно, но на рубежах пакетов
сбрасывается до пакетной, и сверху упирается в цену «всей сети». Здесь
закреплены ровно те числа, которые видит посетитель в панели выбора.
"""

from __future__ import annotations

from config.ad_landing import PRICE_SINGLE_RUB, build_price_table, cap_starts_at, quote_price


def test_below_first_package_is_plain_per_item():
    assert quote_price(1)["price"] == PRICE_SINGLE_RUB
    assert quote_price(4)["price"] == 4 * PRICE_SINGLE_RUB


def test_fifth_community_drops_price_to_package():
    """Пятое сообщество ДЕШЕВЛЕ четырёх — в этом и смысл рубежа."""
    assert quote_price(5)["price"] == 1300
    assert quote_price(5)["price"] < quote_price(4)["price"]
    assert quote_price(5)["anchor"] == "Пакет «Куст»"


def test_after_package_price_grows_per_item_again():
    assert quote_price(6)["price"] == 1300 + PRICE_SINGLE_RUB
    assert quote_price(9)["price"] == 1300 + 4 * PRICE_SINGLE_RUB


def test_tenth_community_drops_to_second_package():
    assert quote_price(10)["price"] == 2500
    assert quote_price(10)["price"] < quote_price(9)["price"]
    assert quote_price(11)["price"] == 2500 + PRICE_SINGLE_RUB


def test_price_caps_at_whole_network():
    """Дойдя до 5000 ₽, цена больше не растёт — сколько ни добавляй."""
    cap = cap_starts_at()
    assert cap == 18
    assert quote_price(cap)["price"] == 5000
    assert quote_price(cap)["capped"] is True
    assert quote_price(cap + 5)["price"] == 5000
    assert quote_price(200)["price"] == 5000


def test_price_never_exceeds_whole_network():
    assert all(row["price"] <= 5000 for row in build_price_table())


def test_saved_is_measured_against_per_item_price():
    q = quote_price(10)
    assert q["saved"] == 10 * PRICE_SINGLE_RUB - 2500
    assert q["percent"] == 29


def test_no_discount_before_first_package():
    assert quote_price(3)["saved"] == 0
    assert quote_price(3)["percent"] == 0


def test_zero_selection_is_free():
    assert quote_price(0)["price"] == 0


def test_price_table_rows_match_quote():
    table = build_price_table(12)
    assert len(table) == 12
    assert table[0]["n"] == 1
    assert [r["price"] for r in table] == [quote_price(n)["price"] for n in range(1, 13)]
