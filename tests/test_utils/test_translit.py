"""Tests for utils.translit.slugify_cyrillic."""

from __future__ import annotations

from utils.translit import slugify_cyrillic


def test_simple_cyrillic_city():
    assert slugify_cyrillic("Карачев") == "karachev"


def test_multiword_with_comma():
    assert slugify_cyrillic("Малмыж, Кировская область") == "malmyzh-kirovskaya-oblast"


def test_mixed_latin_and_digits():
    assert slugify_cyrillic("Yoshkar-Ola 42") == "yoshkar-ola-42"


def test_empty_input():
    assert slugify_cyrillic("") == ""
    assert slugify_cyrillic("   ") == ""


def test_only_punctuation_and_spaces():
    assert slugify_cyrillic("   !!! ,,, ") == ""


def test_uppercase_normalized():
    assert slugify_cyrillic("МАЛМЫЖ") == "malmyzh"


def test_hard_and_soft_signs_dropped():
    assert slugify_cyrillic("объявление") == "obyavlenie"
    # ь between letters → no separator
    assert slugify_cyrillic("Льгов") == "lgov"


def test_yo_and_ts_and_shch():
    assert slugify_cyrillic("Ёлки") == "yolki"
    assert slugify_cyrillic("Целинный") == "tselinnyy"
    assert slugify_cyrillic("Защита") == "zashchita"


def test_collapses_separators():
    assert slugify_cyrillic("a---b___c   d") == "a-b-c-d"


def test_strips_edges():
    assert slugify_cyrillic("---Карачев---") == "karachev"
